-- Migration number: 0001 	 initial schema

CREATE TABLE IF NOT EXISTS expenses (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    amount_cents INTEGER NOT NULL,
    currency     TEXT    NOT NULL,
    description  TEXT,
    merchant     TEXT,
    category     TEXT    NOT NULL DEFAULT 'uncategorized',
    card         TEXT,
    source       TEXT    NOT NULL CHECK (source IN ('telegram', 'shortcut')),
    -- UTC, 'YYYY-MM-DD HH:MM:SS' so string comparison == chronological order
    spent_at     TEXT    NOT NULL,
    created_at   TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%d %H:%M:%S', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_expenses_spent_at ON expenses (spent_at);

-- Learned merchant -> category mapping, used to auto-categorize Shortcut expenses.
CREATE TABLE IF NOT EXISTS merchant_rules (
    merchant TEXT PRIMARY KEY, -- lower-cased
    category TEXT NOT NULL
);
