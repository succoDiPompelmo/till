"""HTTP routing, independent of the Workers runtime so it can be tested with plain Python."""

import csv
import hmac
import io
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from .bot import Bot, Call, expense_logged_calls
from .config import Config
from .money import AmountError, parse_amount
from .repository import Repository
from .timeutil import from_db, get_zone, parse_iso, period_bounds, to_db


@dataclass
class HttpResult:
    status: int
    body: Any = None  # dict/list -> JSON, str -> text
    content_type: str | None = None
    telegram_calls: list[Call] = field(default_factory=list)


def _safe_equals(a: str, b: str) -> bool:
    return bool(a) and bool(b) and hmac.compare_digest(a.encode(), b.encode())


def _bearer(headers: dict[str, str]) -> str:
    auth = headers.get("authorization", "")
    return auth[7:].strip() if auth.lower().startswith("bearer ") else ""


def _clean(value: Any, limit: int = 200) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text[:limit] or None


class App:
    def __init__(self, repo: Repository, config: Config, now: datetime | None = None):
        self.repo = repo
        self.config = config
        self.zone = get_zone(config.timezone)
        self._now = now

    @property
    def now(self) -> datetime:
        return self._now or datetime.now(UTC)

    async def handle(
        self, method: str, path: str, query: dict[str, str], headers: dict[str, str], body: str
    ) -> HttpResult:
        headers = {k.lower(): v for k, v in headers.items()}
        path = path.rstrip("/") or "/"
        if method == "GET" and path == "/":
            return HttpResult(200, {"ok": True, "service": "till"})
        if path == "/telegram" and method == "POST":
            return await self._telegram(headers, body)
        if path == "/api/expenses":
            if not _safe_equals(_bearer(headers), self.config.shortcut_token):
                return HttpResult(401, {"error": "unauthorized"})
            if method == "POST":
                return await self._create_expense(body)
            if method == "GET":
                return await self._list_expenses(query)
            return HttpResult(405, {"error": "method not allowed"})
        return HttpResult(404, {"error": "not found"})

    async def _telegram(self, headers: dict[str, str], body: str) -> HttpResult:
        secret = headers.get("x-telegram-bot-api-secret-token", "")
        if not _safe_equals(secret, self.config.telegram_webhook_secret):
            return HttpResult(401, {"error": "unauthorized"})
        try:
            update = json.loads(body)
        except json.JSONDecodeError:
            return HttpResult(400, {"error": "invalid json"})
        calls = await Bot(self.repo, self.config, self._now).handle_update(update)
        # Always 200 so Telegram doesn't keep retrying an update.
        return HttpResult(200, {"ok": True}, telegram_calls=calls)

    async def _create_expense(self, body: str) -> HttpResult:
        """Endpoint for the iPhone Shortcuts automation (Wallet transaction trigger)."""
        try:
            payload = json.loads(body or "{}")
        except json.JSONDecodeError:
            return HttpResult(400, {"error": "body must be JSON"})
        if not isinstance(payload, dict) or payload.get("amount") in (None, ""):
            return HttpResult(400, {"error": "'amount' is required"})
        try:
            cents, currency = parse_amount(payload["amount"], self.config.default_currency)
        except AmountError as e:
            return HttpResult(400, {"error": str(e)})
        if code := _clean(payload.get("currency"), 3):
            currency = code.upper()
        spent = parse_iso(str(payload["date"])) if payload.get("date") else None
        category = _clean(payload.get("category"), 32)
        account = _clean(payload.get("account"), 40)
        if account and await self.repo.resolve_account(account) is None:
            return HttpResult(400, {"error": f"unknown account: {account}"})
        expense = await self.repo.add_expense(
            amount_cents=cents,
            currency=currency,
            merchant=_clean(payload.get("merchant")),
            description=_clean(payload.get("name") or payload.get("note")),
            card=_clean(payload.get("card")),
            category=category.lower() if category else None,
            account=account,
            spent_at=to_db(spent or self.now),
            source="shortcut",
        )
        calls: list[Call] = []
        if self.config.notify_chat_id is not None:
            calls = await expense_logged_calls(
                expense, self.config.notify_chat_id, self.repo, self.zone, prefix="💳 Card payment"
            )
        return HttpResult(201, {"ok": True, "expense": expense.to_dict()}, telegram_calls=calls)

    async def _list_expenses(self, query: dict[str, str]) -> HttpResult:
        try:
            start, end, _ = period_bounds(query.get("period", "month"), self.now, self.zone)
        except ValueError as e:
            return HttpResult(400, {"error": str(e)})
        account = _clean(query.get("account"), 40)
        if account and await self.repo.resolve_account(account) is None:
            return HttpResult(400, {"error": f"unknown account: {account}"})
        expenses = await self.repo.between(to_db(start), to_db(end), account)
        if query.get("format") == "csv":
            out = io.StringIO()
            writer = csv.writer(out)
            writer.writerow(["id", "date", "amount", "currency", "category", "account", "description", "merchant", "card", "source"])
            for e in expenses:
                absolute_cents = abs(e.amount_cents)
                amount = f"{'-' if e.amount_cents < 0 else ''}{absolute_cents // 100}.{absolute_cents % 100:02d}"
                writer.writerow([
                    e.id, from_db(e.spent_at, self.zone).isoformat(), amount, e.currency,
                    e.category, e.account or "", e.description or "", e.merchant or "", e.card or "", e.source,
                ])
            return HttpResult(200, out.getvalue(), content_type="text/csv; charset=utf-8")
        return HttpResult(200, {"expenses": [e.to_dict() for e in expenses]})
