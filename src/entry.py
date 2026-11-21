"""Cloudflare Worker entrypoint: adapts the Workers runtime (Request, D1, fetch) to till.app."""

import json
from urllib.parse import parse_qsl, urlsplit

from js import Object
from pyodide.ffi import jsnull, to_js
from workers import Response, WorkerEntrypoint, fetch

from till.app import App, HttpResult
from till.config import Config
from till.pdf_import import MAX_FILE_SIZE, PROMPT, PdfImportError, PdfImporter
from till.repository import Repository


def _py(value):
    if value is jsnull:
        return None
    if hasattr(value, "to_py"):
        value = value.to_py()
    if isinstance(value, dict):
        return {k: _py(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_py(v) for v in value]
    return value


class D1Database:
    """Implements till.repository.Database on top of a D1 binding."""

    def __init__(self, binding):
        self.binding = binding

    def _stmt(self, sql, params):
        stmt = self.binding.prepare(sql)
        # Pyodide turns None into `undefined`, which D1 rejects; it must be `null`.
        return stmt.bind(*[jsnull if p is None else p for p in params]) if params else stmt

    async def all(self, sql, params=()):
        result = await self._stmt(sql, params).all()
        return _py(result.results) or []

    async def run(self, sql, params=()):
        result = await self._stmt(sql, params).run()
        return int(result.meta.last_row_id)


class WorkerPdfServices:
    def __init__(self, token, ai):
        self.token = token
        self.ai = ai

    async def download(self, file_id):
        if not self.token:
            raise PdfImportError("Telegram is not configured.")
        try:
            response = await fetch(
                f"https://api.telegram.org/bot{self.token}/getFile",
                method="POST",
                headers={"content-type": "application/json"},
                body=json.dumps({"file_id": file_id}),
            )
            if not response.ok:
                raise PdfImportError("Telegram could not locate the file.")
            result = json.loads(await response.text())
            path = result.get("result", {}).get("file_path")
            if not result.get("ok") or not isinstance(path, str) or not path or ".." in path:
                raise PdfImportError("Telegram could not locate the file.")
            response = await fetch(f"https://api.telegram.org/file/bot{self.token}/{path}")
            if not response.ok:
                raise PdfImportError("Telegram could not download the file.")
            length = response.headers.get("content-length")
            if length and int(length) > MAX_FILE_SIZE:
                raise PdfImportError("The PDF exceeds 10 MB.")
            return await response.bytes()
        except PdfImportError:
            raise
        except Exception as exc:
            print(f"Telegram PDF download failed ({type(exc).__name__})")
            raise PdfImportError("Telegram file download failed.") from exc

    async def extract(self, text, image):
        try:
            if image is None:
                model = "@cf/meta/llama-3.3-70b-instruct-fp8-fast"
                payload = {
                    "messages": [
                        {"role": "system", "content": PROMPT},
                        {"role": "user", "content": text},
                    ],
                    "response_format": {"type": "json_object"},
                    "temperature": 0,
                    "max_tokens": 4096,
                }
            else:
                model = "@cf/meta/llama-3.2-11b-vision-instruct"
                payload = {
                    "prompt": PROMPT,
                    "image": list(image),
                    "temperature": 0,
                    "max_tokens": 4096,
                }
            response = _py(await self.ai.run(model, to_js(payload, dict_converter=Object.fromEntries)))
            if not isinstance(response, dict) or "response" not in response:
                raise PdfImportError("AI returned an invalid response; nothing was imported.")
            return response["response"]
        except PdfImportError:
            raise
        except Exception as exc:
            print(f"Workers AI PDF extraction failed ({type(exc).__name__})")
            raise PdfImportError("AI could not process the PDF; nothing was imported.") from exc


async def call_telegram(token, call):
    payload = {k: v for k, v in call.items() if k != "method"}
    resp = await fetch(
        f"https://api.telegram.org/bot{token}/{call['method']}",
        method="POST",
        headers={"content-type": "application/json"},
        body=json.dumps(payload),
    )
    if not resp.ok:
        print(f"Telegram {call['method']} failed: {resp.status} {await resp.text()}")


def to_response(result: HttpResult) -> Response:
    if isinstance(result.body, str):
        body, content_type = result.body, result.content_type or "text/plain; charset=utf-8"
    else:
        body, content_type = json.dumps(result.body), result.content_type or "application/json"
    return Response(body, status=result.status, headers={"content-type": content_type})


class Default(WorkerEntrypoint):
    async def fetch(self, request):
        js_request = getattr(request, "js_object", request)
        config = Config.from_env(self.env)
        url = urlsplit(str(js_request.url))
        headers = {}
        for name in ("authorization", "x-telegram-bot-api-secret-token"):
            value = js_request.headers.get(name)
            if value is not None and value is not jsnull:
                headers[name] = str(value)
        method = str(js_request.method).upper()
        body = await js_request.text() if method in ("POST", "PUT", "PATCH") else ""

        repo = Repository(D1Database(self.env.DB))
        pdf_importer = PdfImporter(repo, WorkerPdfServices(config.telegram_bot_token, self.env.AI), config.timezone)
        app = App(repo, config, pdf_importer=pdf_importer)
        result = await app.handle(method, url.path, dict(parse_qsl(url.query)), headers, body)

        if result.telegram_calls and not config.telegram_bot_token:
            print("TELEGRAM_BOT_TOKEN not set; skipping Telegram replies")
        elif result.telegram_calls:
            for call in result.telegram_calls:
                try:
                    await call_telegram(config.telegram_bot_token, call)
                except Exception as e:  # a failed reply must not fail the request
                    print(f"Telegram {call['method']} error: {e!r}")
        return to_response(result)
