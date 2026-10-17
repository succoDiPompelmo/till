import json

from conftest import CHAT

TG = {"x-telegram-bot-api-secret-token": "hook-secret"}
AUTH = {"Authorization": "Bearer shortcut-token"}


def tg_message(text, chat=CHAT, date=1792245600):  # 2026-10-17 ~ 13:20 UTC
    return json.dumps({"update_id": 1, "message": {"message_id": 5, "date": date, "chat": {"id": chat}, "text": text}})


def send(run, app, text, **kw):
    result = run(app.handle("POST", "/telegram", {}, TG, tg_message(text, **kw)))
    assert result.status == 200
    return result.telegram_calls


def test_telegram_rejects_bad_secret(run, app):
    result = run(app.handle("POST", "/telegram", {}, {"x-telegram-bot-api-secret-token": "nope"}, tg_message("1 x")))
    assert result.status == 401


def test_telegram_ignores_unknown_chat_but_tells_id_on_start(run, app, repo):
    assert send(run, app, "5 coffee", chat=999) == []
    calls = send(run, app, "/start", chat=999)
    assert "999" in calls[0]["text"]
    assert run(repo.latest()) == []


def test_log_expense_with_category(run, app, repo):
    calls = send(run, app, "12.50 lunch #food")
    assert calls[0]["method"] == "sendMessage"
    assert "€12.50" in calls[0]["text"] and "reply_markup" not in calls[0]
    [e] = run(repo.latest())
    assert (e.amount_cents, e.category, e.description, e.source) == (1250, "food", "lunch", "telegram")


def test_uncategorized_expense_gets_keyboard_and_callback_learns(run, app, repo):
    calls = send(run, app, "3 coffee")
    keyboard = calls[0]["reply_markup"]["inline_keyboard"]
    button = next(b for row in keyboard for b in row if b["text"] == "food")
    update = {
        "update_id": 2,
        "callback_query": {
            "id": "cb1",
            "data": button["callback_data"],
            "message": {"message_id": 9, "chat": {"id": CHAT}},
        },
    }
    result = run(app.handle("POST", "/telegram", {}, TG, json.dumps(update)))
    methods = [c["method"] for c in result.telegram_calls]
    assert methods == ["answerCallbackQuery", "editMessageText"]
    assert run(repo.latest())[0].category == "food"
    # learned: next "coffee" is auto-categorized
    send(run, app, "2.5 coffee")
    assert run(repo.latest())[0].category == "food"


def test_summary_undo_del_cat(run, app, repo):
    send(run, app, "10 groceries #food")
    send(run, app, "5,5 bus #transport")
    send(run, app, "20 USD gift #fun")
    text = send(run, app, "/month")[0]["text"]
    assert "October 2026" in text
    assert "Total: €15.50" in text and "Total: $20.00" in text

    assert "Deleted" in send(run, app, "/undo")[0]["text"]
    assert len(run(repo.latest())) == 2

    first = run(repo.latest())[-1]
    assert "Updated" in send(run, app, f"/cat {first.id} groceries")[0]["text"]
    assert run(repo.get(first.id)).category == "groceries"

    assert f"Deleted #{first.id}" in send(run, app, f"/del {first.id}")[0]["text"]
    assert "No expense" in send(run, app, f"/del {first.id}")[0]["text"]
    assert "No expenses" in send(run, app, "/month 2026-01")[0]["text"]
    assert "Unknown period" in send(run, app, "/month nope")[0]["text"]


def test_unparseable_message(run, app):
    assert "couldn't find an amount" in send(run, app, "hello")[0]["text"]


def test_shortcut_requires_token(run, app):
    result = run(app.handle("POST", "/api/expenses", {}, {}, json.dumps({"amount": "5"})))
    assert result.status == 401
    result = run(app.handle("POST", "/api/expenses", {}, {"authorization": "Bearer wrong"}, "{}"))
    assert result.status == 401


def test_shortcut_wallet_transaction(run, app, repo):
    body = {"amount": "12,40 €", "merchant": "Esselunga", "card": "Revolut"}
    result = run(app.handle("POST", "/api/expenses", {}, AUTH, json.dumps(body)))
    assert result.status == 201
    exp = result.body["expense"]
    assert (exp["amount_cents"], exp["currency"], exp["merchant"], exp["source"]) == (1240, "EUR", "Esselunga", "shortcut")
    assert exp["category"] == "uncategorized"
    # notification with category keyboard goes to the notify chat
    [call] = result.telegram_calls
    assert call["chat_id"] == CHAT and "Esselunga" in call["text"] and "reply_markup" in call

    run(repo.set_category(exp["id"], "groceries"))
    result = run(app.handle("POST", "/api/expenses", {}, AUTH, json.dumps({"amount": 3, "merchant": "esselunga "})))
    assert result.body["expense"]["category"] == "groceries"
    assert "reply_markup" not in result.telegram_calls[0]


def test_shortcut_validation(run, app):
    for body in ["not json", "{}", json.dumps({"amount": "abc"}), json.dumps([1])]:
        assert run(app.handle("POST", "/api/expenses", {}, AUTH, body)).status == 400


def test_shortcut_explicit_date_and_list(run, app):
    body = {"amount": "$4", "name": "Snack", "date": "2026-10-01T09:30:00+02:00", "category": "Food"}
    assert run(app.handle("POST", "/api/expenses", {}, AUTH, json.dumps(body))).status == 201
    listed = run(app.handle("GET", "/api/expenses", {"period": "2026-10"}, AUTH, ""))
    [e] = listed.body["expenses"]
    assert (e["spent_at"], e["currency"], e["category"]) == ("2026-10-01 07:30:00", "USD", "food")
    csv_result = run(app.handle("GET", "/api/expenses", {"period": "2026-10", "format": "csv"}, AUTH, ""))
    assert csv_result.content_type.startswith("text/csv")
    assert "2026-10-01T09:30:00+02:00,4.00,USD,food,Snack" in csv_result.body


def test_not_found_and_health(run, app):
    assert run(app.handle("GET", "/", {}, {}, "")).status == 200
    assert run(app.handle("GET", "/nope", {}, {}, "")).status == 404
