"""Data access. All SQL lives here and runs unchanged on D1 and on sqlite3 (tests)."""

from dataclasses import dataclass
from typing import Any, Protocol


class Database(Protocol):
    async def all(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]: ...

    async def run(self, sql: str, params: tuple[Any, ...] = ()) -> int:
        """Execute a write and return the last inserted row id."""
        ...


@dataclass
class Expense:
    id: int
    amount_cents: int
    currency: str
    description: str | None
    merchant: str | None
    category: str
    account: str | None
    card: str | None
    source: str
    spent_at: str

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "Expense":
        return cls(**{f: row.get(f) for f in cls.__dataclass_fields__})

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)

    @property
    def label(self) -> str:
        return self.description or self.merchant or "—"


_COLUMNS = "id, amount_cents, currency, description, merchant, category, account, card, source, spent_at"


class Repository:
    def __init__(self, db: Database):
        self.db = db

    async def add_expense(
        self,
        *,
        amount_cents: int,
        currency: str,
        spent_at: str,
        source: str,
        description: str | None = None,
        merchant: str | None = None,
        category: str | None = None,
        account: str | None = None,
        card: str | None = None,
    ) -> Expense:
        category = category or await self.category_for(merchant or description) or "uncategorized"
        account = await self.resolve_account(account) if account else None
        if account is None and card:
            account = await self.account_for_card(card)
        new_id = await self.db.run(
            "INSERT INTO expenses (amount_cents, currency, description, merchant, category, account, card, source, spent_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (amount_cents, currency, description, merchant, category, account, card, source, spent_at),
        )
        expense = await self.get(new_id)
        assert expense is not None
        return expense

    async def get(self, expense_id: int) -> Expense | None:
        rows = await self.db.all(f"SELECT {_COLUMNS} FROM expenses WHERE id = ?", (expense_id,))
        return Expense.from_row(rows[0]) if rows else None

    async def latest(self, limit: int = 10) -> list[Expense]:
        rows = await self.db.all(
            f"SELECT {_COLUMNS} FROM expenses ORDER BY spent_at DESC, id DESC LIMIT ?", (limit,)
        )
        return [Expense.from_row(r) for r in rows]

    async def last_created(self) -> Expense | None:
        rows = await self.db.all(f"SELECT {_COLUMNS} FROM expenses ORDER BY id DESC LIMIT 1")
        return Expense.from_row(rows[0]) if rows else None

    async def delete(self, expense_id: int) -> bool:
        if await self.get(expense_id) is None:
            return False
        await self.db.run("DELETE FROM expenses WHERE id = ?", (expense_id,))
        return True

    async def set_category(self, expense_id: int, category: str) -> Expense | None:
        expense = await self.get(expense_id)
        if expense is None:
            return None
        await self.db.run("UPDATE expenses SET category = ? WHERE id = ?", (category, expense_id))
        if expense.merchant or expense.description:
            await self.learn_rule(expense.merchant or expense.description, category)
        expense.category = category
        return expense

    async def accounts(self) -> list[str]:
        rows = await self.db.all("SELECT name FROM accounts ORDER BY name COLLATE NOCASE")
        return [r["name"] for r in rows]

    async def account_options(self) -> list[tuple[int, str]]:
        rows = await self.db.all("SELECT id, name FROM accounts ORDER BY name COLLATE NOCASE")
        return [(r["id"], r["name"]) for r in rows]

    async def account_by_id(self, account_id: int) -> str | None:
        rows = await self.db.all("SELECT name FROM accounts WHERE id = ?", (account_id,))
        return rows[0]["name"] if rows else None

    async def resolve_account(self, name: str) -> str | None:
        aliases = {"welfare": "Company Welfare", "company-welfare": "Company Welfare"}
        candidate = aliases.get(name.strip().lower(), name.strip())
        rows = await self.db.all("SELECT name FROM accounts WHERE name = ? COLLATE NOCASE", (candidate,))
        return rows[0]["name"] if rows else None

    async def add_account(self, name: str) -> str:
        await self.db.run(
            "INSERT INTO accounts (name) VALUES (?) ON CONFLICT(name) DO NOTHING", (name.strip(),)
        )
        account = await self.resolve_account(name)
        assert account is not None
        return account

    async def set_account(self, expense_id: int, account: str) -> Expense | None:
        expense = await self.get(expense_id)
        canonical = await self.resolve_account(account)
        if expense is None or canonical is None:
            return None
        await self.db.run("UPDATE expenses SET account = ? WHERE id = ?", (canonical, expense_id))
        if expense.card:
            await self.learn_card_account(expense.card, canonical)
        expense.account = canonical
        return expense

    async def learn_card_account(self, card: str, account: str) -> None:
        canonical = await self.resolve_account(account)
        if canonical is None:
            raise ValueError(f"Unknown account: {account}")
        await self.db.run(
            "INSERT INTO card_account_rules (card, account) VALUES (?, ?)"
            " ON CONFLICT(card) DO UPDATE SET account = excluded.account",
            (card.strip().lower(), canonical),
        )

    async def account_for_card(self, card: str) -> str | None:
        rows = await self.db.all(
            "SELECT account FROM card_account_rules WHERE card = ?", (card.strip().lower(),)
        )
        return rows[0]["account"] if rows else None

    async def set_opening_balance(
        self, account: str, amount_cents: int, currency: str, recorded_at: str
    ) -> bool:
        canonical = await self.resolve_account(account)
        if canonical is None:
            return False
        existing = await self.db.all(
            "SELECT id FROM account_funds WHERE account = ? COLLATE NOCASE"
            " AND currency = ? AND kind = 'opening'",
            (canonical, currency),
        )
        if existing:
            return False
        await self.db.run(
            "INSERT INTO account_funds (account, amount_cents, currency, kind, created_at)"
            " VALUES (?, ?, ?, 'opening', ?)",
            (canonical, amount_cents, currency, recorded_at),
        )
        return True

    async def add_income(
        self,
        account: str,
        amount_cents: int,
        currency: str,
        recorded_at: str,
        description: str | None = None,
    ) -> bool:
        canonical = await self.resolve_account(account)
        if canonical is None or amount_cents <= 0:
            return False
        await self.db.run(
            "INSERT INTO account_funds (account, amount_cents, currency, kind, description, created_at)"
            " VALUES (?, ?, ?, 'income', ?, ?)",
            (canonical, amount_cents, currency, description, recorded_at),
        )
        return True

    async def add_transfer(
        self,
        from_account: str,
        to_account: str,
        amount_cents: int,
        currency: str,
        transferred_at: str,
        description: str | None = None,
    ) -> int | None:
        source = await self.resolve_account(from_account)
        destination = await self.resolve_account(to_account)
        if (
            source is None
            or destination is None
            or source.casefold() == destination.casefold()
            or amount_cents <= 0
        ):
            return None
        return await self.db.run(
            "INSERT INTO transfers (from_account, to_account, amount_cents, currency, description, transferred_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (source, destination, amount_cents, currency, description, transferred_at),
        )

    async def balances(self, default_currency: str) -> list[dict[str, Any]]:
        return await self.db.all(
            "WITH opening AS ("
            " SELECT account, currency, MIN(created_at) AS opened_at FROM account_funds"
            " WHERE kind = 'opening' GROUP BY account COLLATE NOCASE, currency"
            "), deltas AS ("
            " SELECT f.account, f.currency, f.amount_cents AS change_cents FROM account_funds f"
            " JOIN opening o ON f.account = o.account COLLATE NOCASE AND f.currency = o.currency"
            " WHERE f.created_at >= o.opened_at"
            " UNION ALL"
            " SELECT e.account, e.currency, -e.amount_cents FROM expenses e"
            " JOIN opening o ON e.account = o.account COLLATE NOCASE AND e.currency = o.currency"
            " WHERE e.spent_at >= o.opened_at"
            " UNION ALL"
            " SELECT t.from_account, t.currency, -t.amount_cents FROM transfers t"
            " JOIN opening o ON t.from_account = o.account COLLATE NOCASE AND t.currency = o.currency"
            " WHERE t.transferred_at >= o.opened_at"
            " UNION ALL"
            " SELECT t.to_account, t.currency, t.amount_cents FROM transfers t"
            " JOIN opening o ON t.to_account = o.account COLLATE NOCASE AND t.currency = o.currency"
            " WHERE t.transferred_at >= o.opened_at"
            ") SELECT account, currency, SUM(change_cents) AS balance_cents FROM deltas"
            " GROUP BY account COLLATE NOCASE, currency ORDER BY account COLLATE NOCASE, currency"
        )

    async def missing_openings(self, default_currency: str) -> list[dict[str, str]]:
        rows = await self.db.all(
            "WITH active AS ("
            " SELECT account, currency FROM expenses WHERE account IS NOT NULL"
            " UNION SELECT account, currency FROM account_funds"
            " UNION SELECT from_account, currency FROM transfers"
            " UNION SELECT to_account, currency FROM transfers"
            " UNION SELECT name, ? FROM accounts"
            ") SELECT DISTINCT active.account, active.currency FROM active"
            " WHERE NOT EXISTS ("
            " SELECT 1 FROM account_funds f WHERE f.account = active.account COLLATE NOCASE"
            " AND f.currency = active.currency AND f.kind = 'opening'"
            ") ORDER BY active.account COLLATE NOCASE, active.currency",
            (default_currency,),
        )
        return [{"account": r["account"], "currency": r["currency"]} for r in rows]

    async def latest_transfers(self, limit: int = 10) -> list[dict[str, Any]]:
        return await self.db.all(
            "SELECT id, from_account, to_account, amount_cents, currency, description, transferred_at"
            " FROM transfers ORDER BY transferred_at DESC, id DESC LIMIT ?",
            (limit,),
        )

    async def learn_rule(self, merchant: str, category: str) -> None:
        await self.db.run(
            "INSERT INTO merchant_rules (merchant, category) VALUES (?, ?)"
            " ON CONFLICT(merchant) DO UPDATE SET category = excluded.category",
            (merchant.strip().lower(), category),
        )

    async def category_for(self, merchant: str | None) -> str | None:
        if not merchant:
            return None
        rows = await self.db.all(
            "SELECT category FROM merchant_rules WHERE merchant = ?", (merchant.strip().lower(),)
        )
        return rows[0]["category"] if rows else None

    async def categories(self) -> list[str]:
        rows = await self.db.all(
            "SELECT category FROM expenses GROUP BY category ORDER BY COUNT(*) DESC, category"
        )
        return [r["category"] for r in rows]

    async def summary(self, start: str, end: str) -> list[dict[str, Any]]:
        """Totals per (account, currency, category) for spent_at in [start, end)."""
        return await self.db.all(
            "SELECT currency, account, category, SUM(amount_cents) AS total, COUNT(*) AS n FROM expenses"
            " WHERE spent_at >= ? AND spent_at < ?"
            " GROUP BY currency, account, category ORDER BY account, currency, total DESC",
            (start, end),
        )

    async def summary_by_account(self, start: str, end: str) -> list[dict[str, Any]]:
        return await self.db.all(
            "SELECT account, currency, SUM(amount_cents) AS total, COUNT(*) AS n FROM expenses"
            " WHERE spent_at >= ? AND spent_at < ?"
            " GROUP BY account, currency ORDER BY account, currency",
            (start, end),
        )

    async def between(self, start: str, end: str, account: str | None = None) -> list[Expense]:
        if account:
            rows = await self.db.all(
                f"SELECT {_COLUMNS} FROM expenses"
                " WHERE spent_at >= ? AND spent_at < ? AND account = ? COLLATE NOCASE"
                " ORDER BY spent_at",
                (start, end, account),
            )
            return [Expense.from_row(r) for r in rows]
        rows = await self.db.all(
            f"SELECT {_COLUMNS} FROM expenses WHERE spent_at >= ? AND spent_at < ? ORDER BY spent_at",
            (start, end),
        )
        return [Expense.from_row(r) for r in rows]
