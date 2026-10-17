-- Migration number: 0003 	 balances, income and transfers

CREATE TABLE IF NOT EXISTS account_funds (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    account      TEXT    NOT NULL,
    amount_cents INTEGER NOT NULL,
    currency     TEXT    NOT NULL,
    kind         TEXT    NOT NULL CHECK (kind IN ('opening', 'income')),
    description  TEXT,
    created_at   TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%d %H:%M:%S', 'now'))
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_account_funds_opening
    ON account_funds (account COLLATE NOCASE, currency)
    WHERE kind = 'opening';

CREATE TABLE IF NOT EXISTS transfers (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    from_account    TEXT    NOT NULL,
    to_account      TEXT    NOT NULL,
    amount_cents    INTEGER NOT NULL CHECK (amount_cents > 0),
    currency        TEXT    NOT NULL,
    description     TEXT,
    transferred_at TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%d %H:%M:%S', 'now')),
    CHECK (from_account <> to_account COLLATE NOCASE)
);

CREATE INDEX IF NOT EXISTS idx_transfers_transferred_at ON transfers (transferred_at);
