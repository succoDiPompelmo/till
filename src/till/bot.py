"""Telegram bot: turns an Update into a list of Telegram Bot API calls to perform."""

import html
import re
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
<code>8,90 taxi</code>  ·  <code>€30 gift #shopping</code>  ·  <code>20 USD souvenir</code>
Use <code>-</code> for refunds: <code>-15 refund #shopping</code>

<b>Reports</b>
/today · /week · /month · /year — totals by category
/month 2026-09 — a specific month
/last [n] — latest expenses with their ids

<b>Fix things</b>
/undo — delete the last logged expense
/del &lt;id&gt; — delete an expense
/cat &lt;id&gt; &lt;category&gt; — recategorize (learns the merchant)
/categories — categories in use"""

Call = dict[str, Any]  # {"method": "...", **params}

_AMOUNT_TOKEN = re.compile(r"^[-+]?[^\d\s]{0,3}\d[\d.,']*[^\d\s]{0,3}$")
_CATEGORY_RE = re.compile(r"^[\w-]{1,32}$")


@dataclass
class ParsedExpense:
    amount_cents: int
    currency: str
    description: str | None
    category: str | None


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
    words = []
    for word in rest:
        if word.startswith("#") and len(word) > 1:
            category = word[1:].lower()
        else:
            words.append(word)
    return ParsedExpense(cents, currency, " ".join(words) or None, category)


def _message(chat_id: int, text: str, **extra: Any) -> Call:
    return {"method": "sendMessage", "chat_id": chat_id, "text": text, "parse_mode": "HTML", **extra}


def category_keyboard(expense_id: int, categories: list[str]) -> dict[str, Any]:
    merged = list(dict.fromkeys([c for c in categories if c != "uncategorized"] + DEFAULT_CATEGORIES))[:8]
    buttons = [{"text": c, "callback_data": f"cat:{expense_id}:{c}"[:64]} for c in merged]
    return {"inline_keyboard": [buttons[i : i + 4] for i in range(0, len(buttons), 4)]}


def describe(expense: Expense, zone: Any) -> str:
    when = from_db(expense.spent_at, zone)
    parts = [f"<b>{format_money(expense.amount_cents, expense.currency)}</b>", html.escape(expense.label)]
    if expense.description and expense.merchant:
        parts.append(f"@ {html.escape(expense.merchant)}")
    return f"#{expense.id} · {when:%d %b %H:%M} · " + " ".join(parts) + f" · <i>{html.escape(expense.category)}</i>"


async def expense_logged_calls(
    expense: Expense, chat_id: int, repo: Repository, zone: Any, prefix: str = "✅ Logged"
) -> list[Call]:
    text = f"{prefix}\n{describe(expense, zone)}"
    if expense.category == "uncategorized":
        keyboard = category_keyboard(expense.id, await repo.categories())
        return [_message(chat_id, text + "\nPick a category:", reply_markup=keyboard)]
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
        spent = datetime.fromtimestamp(message["date"], UTC) if "date" in message else self.now
        expense = await self.repo.add_expense(
            amount_cents=parsed.amount_cents,
            currency=parsed.currency,
            description=parsed.description,
            category=parsed.category,
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
        return [_message(chat_id, "Unknown command. See /help.")]

    async def _handle_callback(self, cb: dict[str, Any]) -> list[Call]:
        message = cb.get("message") or {}
        chat_id = message.get("chat", {}).get("id")
        answer: Call = {"method": "answerCallbackQuery", "callback_query_id": cb["id"]}
        if chat_id not in self.config.allowed_chat_ids:
            return [answer]
        data = cb.get("data", "")
        match = re.fullmatch(r"cat:(\d+):([\w-]{1,32})", data)
        if not match:
            return [answer]
        expense = await self.repo.set_category(int(match.group(1)), match.group(2))
        if expense is None:
            return [{**answer, "text": "That expense no longer exists."}]
        return [
            {**answer, "text": f"Saved as {expense.category}"},
            {
                "method": "editMessageText",
                "chat_id": chat_id,
                "message_id": message["message_id"],
                "text": f"🏷 Categorized\n{describe(expense, self.zone)}",
                "parse_mode": "HTML",
            },
        ]

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
        for row in rows:
            totals[row["currency"]] = totals.get(row["currency"], 0) + row["total"]
            lines.append(
                f"{html.escape(row['category'])}: {format_money(row['total'], row['currency'])}"
                f" <i>({row['n']})</i>"
            )
        lines.append("—")
        lines.extend(f"<b>Total: {format_money(t, c)}</b>" for c, t in totals.items())
        return "\n".join(lines)
