import json

from conftest import CHAT, NOW

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
    assert "€12.50" in calls[0]["text"] and "reply_markup" in calls[0]
    assert "Pick an account" in calls[0]["text"]
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
    assert methods == ["answerCallbackQuery", "editMessageText", "sendMessage"]
    assert "account for expense" in result.telegram_calls[-1]["text"]
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
    assert "Pick an account" in result.telegram_calls[0]["text"]
    assert "reply_markup" in result.telegram_calls[0]


def test_telegram_account_selection_and_card_learning(run, app, repo):
    calls = send(run, app, "12 lunch #food")
    keyboard = calls[0]["reply_markup"]["inline_keyboard"]
    callback_data = next(
        b["callback_data"]
        for row in keyboard
        for b in row
        if b["text"] == "BBVA"
    )
    expense = run(repo.latest())[0]
    update = {
        "callback_query": {
            "id": "account-callback",
            "data": callback_data,
            "message": {"message_id": 20, "chat": {"id": CHAT}},
        }
    }
    result = run(app.handle("POST", "/telegram", {}, TG, json.dumps(update)))
    assert result.telegram_calls[0]["method"] == "answerCallbackQuery"
    assert run(repo.get(expense.id)).account == "BBVA"

    # Selecting a shortcut card's account teaches the mapping for future charges.
    created = run(
        app.handle(
            "POST",
            "/api/expenses",
            {},
            AUTH,
            json.dumps(
                {"amount": 3, "merchant": "Cafe", "card": "My Revolut Card", "category": "food"}
            ),
        )
    )
    account_prompt = created.telegram_calls[0]["reply_markup"]["inline_keyboard"]
    rev_data = next(
        b["callback_data"]
        for row in account_prompt
        for b in row
        if b["text"] == "Revolut"
    )
    update["callback_query"].update(id="card-account", data=rev_data)
    run(app.handle("POST", "/telegram", {}, TG, json.dumps(update)))
    mapped = run(
        app.handle(
            "POST",
            "/api/expenses",
            {},
            AUTH,
            json.dumps({"amount": 2, "merchant": "Grocer", "card": "my revolut card"}),
        )
    )
    assert mapped.body["expense"]["account"] == "Revolut"
    assert "Pick an account" not in mapped.telegram_calls[0]["text"]


def test_account_text_syntax_and_account_report(run, app, repo):
    send(run, app, "10 lunch #food @bbva")
    send(run, app, "5 lunch #food @revolut")
    send(run, app, "3 lunch #food @welfare")
    summary = send(run, app, "/accounts month")[0]["text"]
    assert "BBVA: €10.00" in summary
    assert "Revolut: €5.00" in summary
    assert "Company Welfare: €3.00" in summary

    monthly = send(run, app, "/month")[0]["text"]
    assert "BBVA total: €10.00" in monthly
    assert "Revolut total: €5.00" in monthly
    assert "Company Welfare" in send(run, app, "/accounts")[0]["text"]

    assert "Travel Card" in send(run, app, "/addaccount Travel Card")[0]["text"]
    assert "Travel Card" in run(repo.accounts())
    assert "will use" in send(run, app, '/mapcard "My card" Travel Card')[0]["text"]
    assert run(repo.account_for_card("my card")) == "Travel Card"


def test_transfers_income_opening_balances_and_refunds(run, app, repo):
    from till.timeutil import to_db

    # Historical expenses are already reflected in the opening balance and must not be deducted again.
    run(
        repo.add_expense(
            amount_cents=9000,
            currency="EUR",
            account="BBVA",
            spent_at="2026-10-17 13:00:00",
            source="telegram",
            description="before opening",
            category="other",
        )
    )
    assert "Opening balance" in send(run, app, '/openingbalance "BBVA" 1000 EUR')[0]["text"]
    send(run, app, "/addaccount Cash")
    assert "Opening balance" in send(run, app, '/openingbalance "Cash" 0 EUR')[0]["text"]
    assert "already exists" in send(run, app, '/openingbalance "BBVA" 2000 EUR')[0]["text"]

    run(
        repo.add_expense(
            amount_cents=1000,
            currency="EUR",
            account="BBVA",
            spent_at=to_db(NOW),
            source="telegram",
            description="purchase",
            category="other",
        )
    )
    run(
        repo.add_expense(
            amount_cents=-500,
            currency="EUR",
            account="BBVA",
            spent_at=to_db(NOW),
            source="telegram",
            description="refund",
            category="other",
        )
    )
    assert "Income recorded" in send(run, app, '/income "BBVA" 100 EUR salary')[0]["text"]
    transfer = send(run, app, '/transfer "BBVA" "Cash" 25 EUR')[0]["text"]
    assert "Not counted as spending" in transfer
    assert "different" in send(run, app, '/transfer "BBVA" "BBVA" 10')[0]["text"]
    assert "positive" in send(run, app, '/transfer "BBVA" "Cash" -1')[0]["text"]

    balances = send(run, app, "/balance")[0]["text"]
    assert "BBVA: €1,070.00" in balances
    assert "Cash: €25.00" in balances
    assert "Opening balance not set" in balances
    [cash_transfer] = run(repo.latest_transfers())
    assert (cash_transfer["from_account"], cash_transfer["to_account"], cash_transfer["amount_cents"]) == (
        "BBVA",
        "Cash",
        2500,
    )
    assert len(run(repo.between("2026-10-17 00:00:00", "2026-10-18 00:00:00"))) == 3
    assert "BBVA → Cash" in send(run, app, "/transfers")[0]["text"]


def test_shortcut_validation(run, app):
    for body in ["not json", "{}", json.dumps({"amount": "abc"}), json.dumps([1])]:
        assert run(app.handle("POST", "/api/expenses", {}, AUTH, body)).status == 400


def test_shortcut_explicit_date_and_list(run, app):
    body = {
        "amount": "$4",
        "name": "Snack",
        "date": "2026-10-01T09:30:00+02:00",
        "category": "Food",
        "account": "Edenred",
    }
    assert run(app.handle("POST", "/api/expenses", {}, AUTH, json.dumps(body))).status == 201
    listed = run(app.handle("GET", "/api/expenses", {"period": "2026-10"}, AUTH, ""))
    [e] = listed.body["expenses"]
    assert (e["spent_at"], e["currency"], e["category"], e["account"]) == (
        "2026-10-01 07:30:00",
        "USD",
        "food",
        "Edenred",
    )
    csv_result = run(app.handle("GET", "/api/expenses", {"period": "2026-10", "format": "csv"}, AUTH, ""))
    assert csv_result.content_type.startswith("text/csv")
    assert "2026-10-01T09:30:00+02:00,4.00,USD,food,Edenred,Snack" in csv_result.body


def test_account_filter_and_invalid_account(run, app):
    run(
        app.handle(
            "POST",
            "/api/expenses",
            {},
            AUTH,
            json.dumps({"amount": "5", "account": "BBVA"}),
        )
    )
    result = run(app.handle("GET", "/api/expenses", {"period": "month", "account": "Edenred"}, AUTH, ""))
    assert result.body["expenses"] == []
    invalid = run(app.handle("GET", "/api/expenses", {"account": "Unknown"}, AUTH, ""))
    assert invalid.status == 400


def test_not_found_and_health(run, app):
    assert run(app.handle("GET", "/", {}, {}, "")).status == 200
    assert run(app.handle("GET", "/nope", {}, {}, "")).status == 404
