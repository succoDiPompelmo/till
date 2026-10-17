import asyncio
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from till.app import App
from till.config import Config
from till.repository import Repository

MIGRATIONS = Path(__file__).parent.parent / "migrations"
NOW = datetime(2026, 10, 17, 14, 0, tzinfo=UTC)  # 16:00 in Europe/Rome
CHAT = 111


class SqliteDatabase:
    def __init__(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        for f in sorted(MIGRATIONS.glob("*.sql")):
            self.conn.executescript(f.read_text(encoding="utf-8"))

    async def all(self, sql, params=()):
        return [dict(r) for r in self.conn.execute(sql, params).fetchall()]

    async def run(self, sql, params=()):
        cur = self.conn.execute(sql, params)
        self.conn.commit()
        return cur.lastrowid


@pytest.fixture
def config():
    return Config(
        default_currency="EUR",
        timezone="Europe/Rome",
        telegram_bot_token="bot-token",
        telegram_webhook_secret="hook-secret",
        allowed_chat_ids={CHAT},
        shortcut_token="shortcut-token",
        notify_chat_id=CHAT,
    )


@pytest.fixture
def repo():
    return Repository(SqliteDatabase())


@pytest.fixture
def app(repo, config):
    return App(repo, config, now=NOW)


@pytest.fixture
def run():
    return asyncio.run
