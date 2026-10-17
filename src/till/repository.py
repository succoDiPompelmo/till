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


_COLUMNS = "id, amount_cents, currency, description, merchant, category, card, source, spent_at"


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
        card: str | None = None,
    ) -> Expense:
        category = category or await self.category_for(merchant or description) or "uncategorized"
        new_id = await self.db.run(
            "INSERT INTO expenses (amount_cents, currency, description, merchant, category, card, source, spent_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (amount_cents, currency, description, merchant, category, card, source, spent_at),
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
        """Totals per (currency, category) for spent_at in [start, end)."""
        return await self.db.all(
            "SELECT currency, category, SUM(amount_cents) AS total, COUNT(*) AS n FROM expenses"
            " WHERE spent_at >= ? AND spent_at < ?"
            " GROUP BY currency, category ORDER BY currency, total DESC",
            (start, end),
        )

    async def between(self, start: str, end: str) -> list[Expense]:
        rows = await self.db.all(
            f"SELECT {_COLUMNS} FROM expenses WHERE spent_at >= ? AND spent_at < ? ORDER BY spent_at",
            (start, end),
        )
        return [Expense.from_row(r) for r in rows]
