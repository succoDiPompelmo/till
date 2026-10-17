"""Telegram bot: turns an Update into a list of Telegram Bot API calls to perform."""

import html
import re
import shlex
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from .config import Config
from .money import AmountError, as_currency_code, format_money, parse_amount
from .repository import Expense, Repository
from .timeutil import from_db, get_zone, period_bounds, to_db

DEFAULT_CATEGORIES = ["food", "groceries", "transport", "shopping", "bills", "health", "fun", "travel"]

HELP = """<b>Till</b> — expense tracker

<b>Log an expense</b>
<code>12.50 lunch with Marco #food</code>
<code>12.50 lunch #food @revolut</code>
<code>8,90 taxi</code>  ·  <code>€30 gift #shopping</code>  ·  <code>20 USD souvenir</code>
Use <code>-</code> for refunds: <code>-15 refund #shopping</code>

<b>Reports</b>
/today · /week · /month · /year — totals by account and category
/month 2026-09 — a specific month
/accounts [period] — list accounts or report spending by account
/last [n] — latest expenses with their ids

<b>Fix things</b>
/undo — delete the last logged expense
/del &lt;id&gt; — delete an expense
/cat &lt;id&gt; &lt;category&gt; — recategorize (learns the merchant)
/account &lt;id&gt; &lt;name&gt; — assign an account to an expense
/addaccount &lt;name&gt; — add an account
/mapcard "&lt;card&gt;" &lt;account&gt; — remember which account a card uses
/openingbalance "&lt;account&gt;" &lt;amount&gt; [currency] — set its starting balance once
/income "&lt;account&gt;" &lt;amount&gt; [currency] [note] — record money in
/transfer "&lt;from&gt;" "&lt;to&gt;" &lt;amount&gt; [currency] — move money, not an expense
/balance — current balance of each account
/transfers [n] — recent transfers
/categories — categories in use"""

Call = dict[str, Any]  # {"method": "...", **params}

_AMOUNT_TOKEN = re.compile(r"^[-+]?[^\d\s]{0,3}\d[\d.,']*[^\d\s]{0,3}$")
_CATEGORY_RE = re.compile(r"^[\w-]{1,32}$")
_ACCOUNT_RE = re.compile(r"^@([\w-]{1,32})$")


@dataclass
class ParsedExpense:
    amount_cents: int
    currency: str
    description: str | None
    category: str | None
    account: str | None


def parse_expense_text(text: str, default_currency: str) -> ParsedExpense | None:
    """Parse '<amount> [CUR] description [#category]'; the amount may also come last."""
    tokens = text.split()
    if not tokens:
        return None
    if _AMOUNT_TOKEN.match(tokens[0]):
        amount_token, rest = tokens[0], tokens[1:]
    elif _AMOUNT_TOKEN.match(tokens[-1]):
        amount_token, rest = tokens[-1], tokens[:-1]
    else:
        return None
    try:
        cents, currency = parse_amount(amount_token, default_currency)
    except AmountError:
        return None
    if rest and (code := as_currency_code(rest[0])):
        currency, rest = code, rest[1:]
    category = None
    account = None
    words = []
    for word in rest:
        if word.startswith("#") and len(word) > 1:
            category = word[1:].lower()
        elif match := _ACCOUNT_RE.fullmatch(word):
            account = match.group(1).lower()
        else:
            words.append(word)
    return ParsedExpense(cents, currency, " ".join(words) or None, category, account)


def _message(chat_id: int, text: str, **extra: Any) -> Call:
    return {"method": "sendMessage", "chat_id": chat_id, "text": text, "parse_mode": "HTML", **extra}


def category_keyboard(expense_id: int, categories: list[str]) -> dict[str, Any]:
    merged = list(dict.fromkeys([c for c in categories if c != "uncategorized"] + DEFAULT_CATEGORIES))[:8]
    buttons = [{"text": c, "callback_data": f"cat:{expense_id}:{c}"[:64]} for c in merged]
    return {"inline_keyboard": [buttons[i : i + 4] for i in range(0, len(buttons), 4)]}


def account_keyboard(expense_id: int, accounts: list[tuple[int, str]]) -> dict[str, Any]:
    buttons = [
        {"text": name, "callback_data": f"account:{expense_id}:{account_id}"}
        for account_id, name in accounts
    ]
    return {"inline_keyboard": [buttons[i : i + 2] for i in range(0, len(buttons), 2)]}


def describe(expense: Expense, zone: Any) -> str:
    when = from_db(expense.spent_at, zone)
    parts = [f"<b>{format_money(expense.amount_cents, expense.currency)}</b>", html.escape(expense.label)]
    if expense.description and expense.merchant:
        parts.append(f"@ {html.escape(expense.merchant)}")
    if expense.account:
        parts.append(f"· {html.escape(expense.account)}")
    return f"#{expense.id} · {when:%d %b %H:%M} · " + " ".join(parts) + f" · <i>{html.escape(expense.category)}</i>"


async def expense_logged_calls(
    expense: Expense, chat_id: int, repo: Repository, zone: Any, prefix: str = "✅ Logged"
) -> list[Call]:
    text = f"{prefix}\n{describe(expense, zone)}"
    if expense.category == "uncategorized":
        keyboard = category_keyboard(expense.id, await repo.categories())
        return [_message(chat_id, text + "\nPick a category:", reply_markup=keyboard)]
    if expense.account is None:
        return [
            _message(
                chat_id,
                text + "\nPick an account:",
                reply_markup=account_keyboard(expense.id, await repo.account_options()),
            )
        ]
    return [_message(chat_id, text)]


class Bot:
    def __init__(self, repo: Repository, config: Config, now: datetime | None = None):
        self.repo = repo
        self.config = config
        self.zone = get_zone(config.timezone)
        self._now = now

    @property
    def now(self) -> datetime:
        return self._now or datetime.now(UTC)

    async def handle_update(self, update: dict[str, Any]) -> list[Call]:
        if cb := update.get("callback_query"):
            return await self._handle_callback(cb)
        message = update.get("message")
        if not message or "text" not in message:
            return []
        chat_id = message["chat"]["id"]
        text = message["text"].strip()
        if chat_id not in self.config.allowed_chat_ids:
            if text.startswith("/start"):
                return [_message(chat_id, f"Not authorized. Add chat id <code>{chat_id}</code> to ALLOWED_CHAT_IDS.")]
            return []
        if text.startswith("/"):
            return await self._handle_command(chat_id, text)
        parsed = parse_expense_text(text, self.config.default_currency)
        if parsed is None:
            return [_message(chat_id, "🤔 I couldn't find an amount. Try <code>12.50 lunch #food</code> or /help.")]
        if parsed.account and await self.repo.resolve_account(parsed.account) is None:
            accounts = ", ".join(await self.repo.accounts())
            return [
                _message(
                    chat_id,
                    f"Unknown account <code>{html.escape(parsed.account)}</code>. "
                    f"Available: {html.escape(accounts)}.",
                )
            ]
        spent = datetime.fromtimestamp(message["date"], UTC) if "date" in message else self.now
        expense = await self.repo.add_expense(
            amount_cents=parsed.amount_cents,
            currency=parsed.currency,
            description=parsed.description,
            category=parsed.category,
            account=parsed.account,
            spent_at=to_db(spent),
            source="telegram",
        )
        return await expense_logged_calls(expense, chat_id, self.repo, self.zone)

    async def _handle_command(self, chat_id: int, text: str) -> list[Call]:
        command, *args = text.split()
        command = command.split("@", 1)[0].lower()
        if command in ("/start", "/help"):
            return [_message(chat_id, HELP)]
        if command in ("/today", "/week", "/month", "/year"):
            period = args[0] if command == "/month" and args else command[1:]
            return [_message(chat_id, await self.summary(period))]
        if command == "/last":
            n = int(args[0]) if args and args[0].isdigit() else 10
            expenses = await self.repo.latest(min(max(n, 1), 50))
            body = "\n".join(describe(e, self.zone) for e in expenses) or "No expenses yet."
            return [_message(chat_id, body)]
        if command == "/undo":
            expense = await self.repo.last_created()
            if expense is None:
                return [_message(chat_id, "Nothing to undo.")]
            await self.repo.delete(expense.id)
            return [_message(chat_id, f"🗑 Deleted\n{describe(expense, self.zone)}")]
        if command == "/del":
            if not args or not args[0].lstrip("#").isdigit():
                return [_message(chat_id, "Usage: /del &lt;id&gt;")]
            expense_id = int(args[0].lstrip("#"))
            deleted = await self.repo.delete(expense_id)
            return [_message(chat_id, f"🗑 Deleted #{expense_id}" if deleted else f"No expense #{expense_id}.")]
        if command == "/cat":
            if len(args) != 2 or not args[0].lstrip("#").isdigit() or not _CATEGORY_RE.match(args[1].lstrip("#")):
                return [_message(chat_id, "Usage: /cat &lt;id&gt; &lt;category&gt;")]
            expense = await self.repo.set_category(int(args[0].lstrip("#")), args[1].lstrip("#").lower())
            if expense is None:
                return [_message(chat_id, f"No expense #{args[0]}.")]
            return [_message(chat_id, f"🏷 Updated\n{describe(expense, self.zone)}")]
        if command == "/categories":
            cats = await self.repo.categories()
            return [_message(chat_id, ", ".join(cats) if cats else "No categories yet.")]
        if command == "/accounts":
            if not args:
                return [_message(chat_id, "Accounts: " + ", ".join(await self.repo.accounts()))]
            period = args[0]
            return [_message(chat_id, await self.account_summary(period))]
        if command == "/addaccount":
            name = " ".join(args).strip()
            if not name or len(name) > 40:
                return [_message(chat_id, "Usage: /addaccount &lt;name&gt; (up to 40 characters)")]
            account = await self.repo.add_account(name)
            return [_message(chat_id, f"✅ Account added: <b>{html.escape(account)}</b>")]
        if command == "/account":
            if len(args) < 2 or not args[0].lstrip("#").isdigit():
                return [_message(chat_id, "Usage: /account &lt;expense id&gt; &lt;account name&gt;")]
            expense_id, name = int(args[0].lstrip("#")), " ".join(args[1:])
            account = await self.repo.resolve_account(name)
            if account is None:
                return [_message(chat_id, "Unknown account. Use /accounts or /addaccount.")]
            expense = await self.repo.set_account(expense_id, account)
            if expense is None:
                return [_message(chat_id, f"No expense #{expense_id}.")]
            return [_message(chat_id, f"🏦 Updated\n{describe(expense, self.zone)}")]
        if command == "/mapcard":
            try:
                parts = shlex.split(text)[1:]
            except ValueError:
                parts = []
            if len(parts) < 2:
                return [_message(chat_id, 'Usage: /mapcard "&lt;card name&gt;" &lt;account name&gt;')]
            card, account_name = parts[0], " ".join(parts[1:])
            account = await self.repo.resolve_account(account_name)
            if account is None:
                return [_message(chat_id, "Unknown account. Use /accounts or /addaccount.")]
            await self.repo.learn_card_account(card, account)
            return [
                _message(
                    chat_id,
                    f"✅ Card <b>{html.escape(card)}</b> will use <b>{html.escape(account)}</b>.",
                )
            ]
        if command in ("/openingbalance", "/income", "/transfer"):
            try:
                parts = shlex.split(text)[1:]
            except ValueError:
                parts = []
            if command == "/openingbalance":
                if len(parts) not in (2, 3):
                    return [
                        _message(
                            chat_id,
                            'Usage: /openingbalance "&lt;account&gt;" &lt;amount&gt; [currency]',
                        )
                    ]
                account, value = parts[0], parts[1]
                currency_token = parts[2] if len(parts) == 3 else None
                try:
                    cents, currency = parse_amount(
                        value, self.config.default_currency, allow_zero=True
                    )
                except AmountError as e:
                    return [_message(chat_id, html.escape(str(e)))]
                if currency_token and (code := as_currency_code(currency_token)):
                    currency = code
                elif currency_token:
                    return [_message(chat_id, "Currency must be a 3-letter code, e.g. EUR.")]
                account_name = await self.repo.resolve_account(account)
                if account_name is None:
                    return [_message(chat_id, "Unknown account. Use /accounts or /addaccount.")]
                created = await self.repo.set_opening_balance(
                    account_name, cents, currency, to_db(self.now)
                )
                if not created:
                    return [
                        _message(
                            chat_id,
                            f"An opening balance for {html.escape(account_name)} in {currency} "
                            "already exists; it cannot be overwritten.",
                        )
                    ]
                return [
                    _message(
                        chat_id,
                        f"✅ Opening balance: <b>{format_money(cents, currency)}</b> "
                        f"for <b>{html.escape(account_name)}</b>.",
                    )
                ]
            if command == "/income":
                if len(parts) < 2:
                    return [
                        _message(
                            chat_id,
                            'Usage: /income "&lt;account&gt;" &lt;amount&gt; [currency] [note]',
                        )
                    ]
                account, value = parts[0], parts[1]
                rest = parts[2:]
                currency = self.config.default_currency
                if rest and (code := as_currency_code(rest[0])):
                    currency, rest = code, rest[1:]
                try:
                    cents, parsed_currency = parse_amount(value, currency)
                except AmountError as e:
                    return [_message(chat_id, html.escape(str(e)))]
                if cents < 0:
                    return [_message(chat_id, "Income amount must be positive.")]
                currency = parsed_currency
                account_name = await self.repo.resolve_account(account)
                if account_name is None:
                    return [_message(chat_id, "Unknown account. Use /accounts or /addaccount.")]
                description = " ".join(rest) or None
                await self.repo.add_income(
                    account_name, cents, currency, to_db(self.now), description
                )
                note = f" · {html.escape(description)}" if description else ""
                return [
                    _message(
                        chat_id,
                        f"✅ Income recorded: <b>{format_money(cents, currency)}</b> "
                        f"to <b>{html.escape(account_name)}</b>{note}.",
                    )
                ]
            if len(parts) not in (3, 4):
                return [
                    _message(
                        chat_id,
                        'Usage: /transfer "&lt;from account&gt;" "&lt;to account&gt;" &lt;amount&gt; [currency]',
                    )
                ]
            from_name, to_name, value = parts[:3]
            currency_token = parts[3] if len(parts) == 4 else None
            try:
                cents, currency = parse_amount(value, self.config.default_currency)
            except AmountError as e:
                return [_message(chat_id, html.escape(str(e)))]
            if cents < 0:
                return [_message(chat_id, "Transfer amount must be positive.")]
            if currency_token and (code := as_currency_code(currency_token)):
                currency = code
            elif currency_token:
                return [_message(chat_id, "Currency must be a 3-letter code, e.g. EUR.")]
            canonical_from = await self.repo.resolve_account(from_name)
            canonical_to = await self.repo.resolve_account(to_name)
            transfer_id = await self.repo.add_transfer(
                from_name, to_name, cents, currency, to_db(self.now)
            )
            if transfer_id is None:
                return [
                    _message(
                        chat_id,
                        "Transfer failed: both accounts must exist and be different.",
                    )
                ]
            return [
                _message(
                    chat_id,
                    f"↔️ Transfer #{transfer_id}: <b>{format_money(cents, currency)}</b> "
                    f"from <b>{html.escape(canonical_from or from_name)}</b> "
                    f"to <b>{html.escape(canonical_to or to_name)}</b>. "
                    "Not counted as spending.",
                )
            ]
        if command == "/balance":
            rows = await self.repo.balances(self.config.default_currency)
            lines = ["<b>Account balances</b>"]
            for row in rows:
                lines.append(
                    f"{html.escape(row['account'])}: "
                    f"{format_money(row['balance_cents'], row['currency'])}"
                )
            missing = await self.repo.missing_openings(self.config.default_currency)
            if missing:
                lines.append(
                    "\nOpening balance not set: "
                    + ", ".join(
                        f"{html.escape(row['account'])} ({html.escape(row['currency'])})"
                        for row in missing
                    )
                )
            return [_message(chat_id, "\n".join(lines))]
        if command == "/transfers":
            limit = int(args[0]) if args and args[0].isdigit() else 10
            rows = await self.repo.latest_transfers(min(max(limit, 1), 50))
            lines = [
                f"#{row['id']} · {html.escape(row['transferred_at'])} · "
                f"{html.escape(row['from_account'])} → {html.escape(row['to_account'])} · "
                f"{format_money(row['amount_cents'], row['currency'])}"
                for row in rows
            ]
            return [_message(chat_id, "\n".join(lines) if lines else "No transfers yet.")]
        return [_message(chat_id, "Unknown command. See /help.")]

    async def _handle_callback(self, cb: dict[str, Any]) -> list[Call]:
        message = cb.get("message") or {}
        chat_id = message.get("chat", {}).get("id")
        answer: Call = {"method": "answerCallbackQuery", "callback_query_id": cb["id"]}
        if chat_id not in self.config.allowed_chat_ids:
            return [answer]
        data = cb.get("data", "")
        match = re.fullmatch(r"cat:(\d+):([\w-]{1,32})", data)
        account_match = re.fullmatch(r"account:(\d+):(\d+)", data)
        if match:
            expense = await self.repo.set_category(int(match.group(1)), match.group(2))
        elif account_match:
            account = await self.repo.account_by_id(int(account_match.group(2)))
            expense = await self.repo.set_account(int(account_match.group(1)), account) if account else None
        else:
            return [answer]
        if expense is None:
            return [{**answer, "text": "That expense or account no longer exists."}]
        is_account = account_match is not None
        saved_value = expense.account if is_account else expense.category
        calls = [
            {**answer, "text": f"Saved as {saved_value}"},
            {
                "method": "editMessageText",
                "chat_id": chat_id,
                "message_id": message["message_id"],
                "text": f"{'🏦 Account set' if is_account else '🏷 Categorized'}\n{describe(expense, self.zone)}",
                "parse_mode": "HTML",
            },
        ]
        if match and expense.account is None:
            calls.append(
                _message(
                    chat_id,
                    f"🏦 Choose an account for expense #{expense.id}:",
                    reply_markup=account_keyboard(expense.id, await self.repo.account_options()),
                )
            )
        return calls

    async def summary(self, period: str) -> str:
        try:
            start, end, label = period_bounds(period, self.now, self.zone)
        except ValueError as e:
            return html.escape(str(e))
        rows = await self.repo.summary(to_db(start), to_db(end))
        if not rows:
            return f"<b>{label}</b>\nNo expenses."
        lines = [f"<b>{label}</b>"]
        totals: dict[str, int] = {}
        account_totals: dict[tuple[str, str], int] = {}
        last_account = None
        for row in rows:
            account = row["account"] or "Unassigned"
            if account != last_account:
                lines.append(f"\n<b>{html.escape(account)}</b>")
                last_account = account
            totals[row["currency"]] = totals.get(row["currency"], 0) + row["total"]
            key = (account, row["currency"])
            account_totals[key] = account_totals.get(key, 0) + row["total"]
            lines.append(
                f"  {html.escape(row['category'])}: {format_money(row['total'], row['currency'])}"
                f" <i>({row['n']})</i>"
            )
        for (account, currency), amount in account_totals.items():
            lines.append(f"<b>{html.escape(account)} total: {format_money(amount, currency)}</b>")
        lines.append("—")
        lines.extend(f"<b>Total: {format_money(t, c)}</b>" for c, t in totals.items())
        return "\n".join(lines)

    async def account_summary(self, period: str) -> str:
        try:
            start, end, label = period_bounds(period, self.now, self.zone)
        except ValueError as e:
            return html.escape(str(e))
        rows = await self.repo.summary_by_account(to_db(start), to_db(end))
        if not rows:
            return f"<b>{label}</b>\nNo expenses."
        lines = [f"<b>{label} · spending by account</b>"]
        for row in rows:
            account = row["account"] or "Unassigned"
            lines.append(
                f"{html.escape(account)}: {format_money(row['total'], row['currency'])} "
                f"<i>({row['n']})</i>"
            )
        return "\n".join(lines)
