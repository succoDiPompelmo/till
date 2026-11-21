import io
import json
import sqlite3

import pytest
from PIL import Image
from pypdf import PdfReader, PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from conftest import CHAT
from till.app import App
from till.pdf_import import PdfImporter


def text_pdf():
    writer = PdfWriter()
    page = writer.add_blank_page(width=600, height=800)
    font = DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"), NameObject("/BaseFont"): NameObject("/Helvetica")})
    page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})})
    stream = DecodedStreamObject()
    stream.set_data(b"BT /F1 12 Tf 10 700 Td (Statement: 2026-11-12 Amazon EUR 24.90) Tj ET")
    page[NameObject("/Contents")] = writer._add_object(stream)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def image_pdf():
    out = io.BytesIO()
    Image.new("RGB", (600, 800), "white").save(out, format="PDF")
    return out.getvalue()


class Services:
    def __init__(self, data, result):
        self.data = data
        self.result = result
        self.inputs = []

    async def download(self, file_id):
        return self.data

    async def extract(self, text, image):
        self.inputs.append((text, image))
        return self.result


def update(data, caption="", chat=CHAT):
    return json.dumps({
        "message": {
            "chat": {"id": chat},
            "caption": caption,
            "document": {"file_id": "file", "file_name": "statement.pdf", "file_size": len(data), "mime_type": "application/pdf"},
        }
    })


def send(run, app, data, **kwargs):
    return run(app.handle("POST", "/telegram", {}, {"x-telegram-bot-api-secret-token": "hook-secret"}, update(data, **kwargs))).telegram_calls


def test_import_text_with_caption_skips_non_expenses_and_allows_reimport(run, repo, config):
    data = text_pdf()
    rows = [
        {"type": "expense", "date": "2026-11-12", "merchant": "Amazon & Co", "amount_cents": 2490, "currency": "EUR"},
        {"type": "refund", "date": "2026-11-13", "merchant": "Shop", "amount_cents": 500, "currency": "EUR"},
        {"type": "income", "date": "2026-11-14", "merchant": "Employer", "amount_cents": 100000, "currency": "EUR"},
        {"type": "expense", "date": "invalid", "merchant": "Shop", "amount_cents": 1, "currency": "EUR"},
    ]
    service = Services(data, {"transactions": rows})
    app = App(repo, config, pdf_importer=PdfImporter(repo, service, config.timezone))
    calls = send(run, app, data, caption="bbva")
    assert "Imported 2 transactions. Skipped: 2." in calls[0]["text"]
    assert "2026-11-12" in calls[0]["text"] and "Amazon &amp; Co" in calls[0]["text"]
    expenses = run(repo.latest())
    assert {(e.amount_cents, e.account) for e in expenses} == {(2490, "BBVA"), (-500, "BBVA")}
    assert service.inputs[0][0] and service.inputs[0][1] is None
    assert "Imported 2 transactions" in send(run, app, data)[0]["text"]
    assert len(run(repo.latest())) == 4
    assert len(service.inputs) == 2


def test_image_pdf_uses_vision_and_unassigned_account(run, repo, config):
    data = image_pdf()
    service = Services(data, json.dumps({"transactions": [{"type": "expense", "date": "2026-01-02", "merchant": "Enel", "amount_cents": 2000, "currency": "EUR"}]}))
    app = App(repo, config, pdf_importer=PdfImporter(repo, service, config.timezone))
    assert "Imported 1 transactions" in send(run, app, data)[0]["text"]
    assert service.inputs[0][0] is None and service.inputs[0][1]
    assert run(repo.latest())[0].account is None


def test_errors_and_unauthorized_do_not_import(run, repo, config):
    data = text_pdf()
    service = Services(data, "not json")
    app = App(repo, config, pdf_importer=PdfImporter(repo, service, config.timezone))
    assert send(run, app, data, chat=999) == []
    assert service.inputs == []
    assert "Unknown account" in send(run, app, data, caption="unknown")[0]["text"]
    assert "invalid response" in send(run, app, data)[0]["text"]
    assert run(repo.latest()) == []


def test_long_result_is_split_without_losing_transactions(run, repo, config):
    data = text_pdf()
    rows = [{"type": "expense", "date": "2026-01-02", "merchant": f"Merchant {n} " + "x" * 100, "amount_cents": 1, "currency": "EUR"} for n in range(60)]
    app = App(repo, config, pdf_importer=PdfImporter(repo, Services(data, {"transactions": rows}), config.timezone))
    calls = send(run, app, data)
    assert len(calls) > 1 and all(len(call["text"]) <= 4000 for call in calls)
    assert all(f"Merchant {n} " in "\n".join(call["text"] for call in calls) for n in range(60))


def test_import_is_atomic(run, repo):
    item = {"amount_cents": 1, "currency": "EUR", "merchant": "Shop", "account": None, "spent_at": "2026-01-02 12:00:00"}
    invalid = {**item, "amount_cents": None}
    with pytest.raises(sqlite3.IntegrityError):
        run(repo.import_pdf([item, invalid]))
    assert run(repo.latest()) == []
    assert len(run(repo.import_pdf([item]))) == 1
    assert not repo.db.conn.in_transaction


def test_failed_second_page_does_not_import_first(run, repo, config):
    reader = PdfReader(io.BytesIO(text_pdf()))
    writer = PdfWriter()
    writer.add_page(reader.pages[0])
    writer.add_page(reader.pages[0])
    out = io.BytesIO()
    writer.write(out)
    data = out.getvalue()

    class FailingServices(Services):
        async def extract(self, text, image):
            self.inputs.append((text, image))
            if len(self.inputs) == 2:
                return "invalid"
            return {"transactions": [{"type": "expense", "date": "2026-01-02", "merchant": "Shop", "amount_cents": 10, "currency": "EUR"}]}

    service = FailingServices(data, None)
    app = App(repo, config, pdf_importer=PdfImporter(repo, service, config.timezone))
    assert "nothing was imported" in send(run, app, data)[0]["text"]
    assert run(repo.latest()) == []
