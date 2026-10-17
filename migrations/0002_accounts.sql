-- Migration number: 0002 	 add payment accounts

ALTER TABLE expenses ADD COLUMN account TEXT;

CREATE TABLE IF NOT EXISTS accounts (
    id   INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE COLLATE NOCASE
);

INSERT OR IGNORE INTO accounts (name) VALUES
    ('BBVA'),
    ('Revolut'),
    ('Company Welfare'),
    ('Edenred');

CREATE TABLE IF NOT EXISTS card_account_rules (
    card    TEXT PRIMARY KEY, -- lower-cased card/pass label
    account TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_expenses_account ON expenses (account);
