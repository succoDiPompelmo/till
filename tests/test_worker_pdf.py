import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def worker(monkeypatch):
    js = SimpleNamespace(Object=SimpleNamespace(fromEntries=lambda value: value))
    ffi = SimpleNamespace(jsnull=object(), to_js=lambda value, **kwargs: value)
    workers = SimpleNamespace(Response=object, WorkerEntrypoint=object, fetch=None)
    monkeypatch.setitem(sys.modules, "js", js)
    monkeypatch.setitem(sys.modules, "pyodide.ffi", ffi)
    monkeypatch.setitem(sys.modules, "workers", workers)
    spec = importlib.util.spec_from_file_location("test_worker_entry", Path(__file__).parent.parent / "src" / "entry.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_telegram_download_uses_worker_response_bytes(run, worker, monkeypatch):
    class Response:
        ok = True
        headers = {"content-length": "8"}

        async def text(self):
            return json.dumps({"ok": True, "result": {"file_path": "documents/file.pdf"}})

        async def bytes(self):
            return b"%PDF-123"

    requests = []

    async def fetch(url, **kwargs):
        requests.append((url, kwargs))
        return Response()

    monkeypatch.setattr(worker, "fetch", fetch)
    service = worker.WorkerPdfServices("test-token", None)
    assert run(service.download("file-id")) == b"%PDF-123"
    assert json.loads(requests[0][1]["body"]) == {"file_id": "file-id"}
    assert requests[1][0].endswith("/documents/file.pdf")


def test_ai_model_payloads(run, worker):
    class AI:
        def __init__(self):
            self.requests = []

        async def run(self, model, payload):
            self.requests.append((model, payload))
            return {"response": '{"transactions":[]}'}

    ai = AI()
    service = worker.WorkerPdfServices("token", ai)
    assert run(service.extract("page text", None)) == '{"transactions":[]}'
    assert run(service.extract(None, b"image")) == '{"transactions":[]}'
    assert ai.requests[0][1]["messages"][1]["content"] == "page text"
    assert ai.requests[0][1]["response_format"] == {"type": "json_object"}
    assert ai.requests[1][1]["image"] == list(b"image")
