# Till 💸

Personal expense tracker running entirely on Cloudflare — a **Python Worker** with a **D1** (SQLite) database.

Two ways to log expenses:

- **Telegram bot** — for anything not paid by Apple Pay (cash, bank transfers, online…): just send `12.50 lunch #food`.
- **iPhone Shortcuts automation** — every Apple Pay payment is logged automatically through the Wallet *Transaction* trigger.

Uncategorized expenses get an inline keyboard in Telegram; tapping a category also *teaches* Till that merchant/description, so the next payment at the same place is categorized automatically.

```
Telegram ──webhook──▶ POST /telegram ─┐
iPhone Shortcut ────▶ POST /api/expenses ─┼─▶ Python Worker ─▶ D1
Spreadsheet/CSV ◀──── GET  /api/expenses ─┘         │
                                                     └─▶ Telegram Bot API (replies / notifications)
```

## Project layout

| Path | What |
| --- | --- |
| `src/entry.py` | Worker entrypoint: adapts the Workers runtime (Request, D1, `fetch`) to the app |
| `src/till/app.py` | HTTP routing, auth, Shortcut endpoint, CSV export |
| `src/till/bot.py` | Telegram message/command handling |
| `src/till/repository.py` | All SQL (runs on D1 in prod and sqlite3 in tests) |
| `src/till/money.py` | Amount parsing (`12,50 €`, `$1,234.56`, …) and formatting |
| `migrations/` | D1 schema migrations |
| `scripts/set_webhook.py` | Registers the Telegram webhook and command menu |

## Prerequisites

- [uv](https://docs.astral.sh/uv/) and **Node.js ≥ 22** (required by Wrangler)
- A Cloudflare account (`npx wrangler login`)
- A Telegram bot token from [@BotFather](https://t.me/BotFather) (`/newbot`)

## Deploy

```powershell
# 1. Create the database and paste the printed database_id into wrangler.jsonc
npx wrangler d1 create till

# 2. Create the tables
npx wrangler d1 migrations apply till --remote

# 3. Deploy the Worker once so it exists on Cloudflare
uv run pywrangler deploy

# 4. Add secrets to the deployed Worker. Wrangler prompts for each value.
npx wrangler secret put TELEGRAM_BOT_TOKEN       # from BotFather
npx wrangler secret put TELEGRAM_WEBHOOK_SECRET  # random; Telegram sends it back on every webhook call
npx wrangler secret put SHORTCUT_TOKEN           # random; used by the iPhone Shortcut
npx wrangler secret put ALLOWED_CHAT_IDS         # your Telegram chat id(s), comma-separated (see below)

# 5. Point Telegram at the Worker (script prompts for the bot token and webhook secret without echoing them)
uv run scripts/set_webhook.py https://till.<your-subdomain>.workers.dev
```

Keep the bot token and webhook secret somewhere secure, such as a password manager: Cloudflare stores them as Worker secrets, but does not show their values later. The webhook setup script prompts for them interactively and does not save them to a file.

**Finding your chat id:** initially set `ALLOWED_CHAT_IDS` to a placeholder (for example `0`), then register the webhook and send `/start` to your bot. The bot replies with your chat id even though it is not yet authorized. Replace the placeholder by running `npx wrangler secret put ALLOWED_CHAT_IDS` again with your actual chat id(s), comma-separated.

Optional settings (`vars` in `wrangler.jsonc`, or secrets):

| Name | Default | Meaning |
| --- | --- | --- |
| `DEFAULT_CURRENCY` | `EUR` | Used when an amount has no symbol/code |
| `TIMEZONE` | `Europe/Rome` | Used for `/today`, `/month`, … and CSV dates |
| `NOTIFY_CHAT_ID` | the only allowed chat | Chat that receives a message for each Shortcut-logged payment |

## Telegram usage

| Message | Effect |
| --- | --- |
| `12.50 lunch with Marco #food` | Logs €12.50, description "lunch with Marco", category `food` |
| `8,90 taxi` / `taxi 8,90` | Amount can come first or last; comma or dot decimals |
| `20 USD souvenir`, `£3 coffee` | Other currencies (totals are kept per currency) |
| `-15 refund #shopping` | Refunds are negative amounts |
| `12.50 lunch #food @revolut` | Assigns the expense to an account; omit `@...` to choose from Telegram buttons |
| `/today` `/week` `/month` `/year` `/month 2026-09` | Totals by account and category (currencies remain separate) |
| `/accounts` / `/accounts month` | List accounts / spending by account for a period |
| `/last [n]` | Latest expenses with their ids |
| `/undo`, `/del <id>` | Delete |
| `/cat <id> <category>` | Recategorize (and remember the merchant/description) |
| `/account <id> <name>` | Assign or change an expense's account |
| `/addaccount <name>` | Add an account |
| `/mapcard "Card or Pass" <account>` | Map an Apple Pay card label to an account |
| `/openingbalance "BBVA" 1200 EUR` | Record BBVA's opening balance for EUR (once per account/currency) |
| `/income "BBVA" 2000 EUR salary` | Record income or a top-up |
| `/transfer "BBVA" "Cash" 100 EUR` | Move money between accounts; transfers are not expenses |
| `/balance` | Show calculated account balances |
| `/transfers [n]` | Show recent transfers |

The initial accounts are **BBVA**, **Revolut**, **Company Welfare**, and **Edenred**. Add more with `/addaccount`. If a Telegram expense has no account, Till asks you to pick one. Existing expenses without an account appear under **Unassigned**; use `/account <id> <name>` to assign them.

For balances, add any accounts you use (for example `/addaccount Cash`) and enter each account's real starting balance with `/openingbalance "Account name" amount [currency]`. This is a baseline as of the moment you run the command: older expenses already in Till are not subtracted again. Set one opening balance for each currency you want tracked. Then record incoming money with `/income`, expenses as usual, and movements between your own accounts with `/transfer`. A cash withdrawal is a transfer (for example BBVA → Cash), not an expense; log the cash purchases separately. `/balance` applies expenses and refunds after each account/currency's opening balance and includes income and transfers. Currencies are calculated independently; the balance may go below zero if the opening balance or a transaction is missing.

## iPhone Shortcuts

Create a personal automation or shortcut that sends an HTTP `POST` request to:

```text
https://till.<your-subdomain>.workers.dev/api/expenses
```

Configure the request with:

- **Authorization header:** name the header `Authorization` and use the standard bearer-token format with your `SHORTCUT_TOKEN` secret as the credential.
- **JSON body:** include an `amount` field, and optionally fields for `merchant` and `card`. Use the variables provided by your chosen Shortcut trigger where available.

Example request body:

```json
{
  "amount": "12,40 €",
  "merchant": "Example Shop",
  "card": "My Revolut Card"
}
```

`amount` is required and can be a number or a currency-formatted string. Optional fields are `currency` (ISO code), `category`, `account` (account name), `name` (description), `note` (description alias), and `date` (ISO 8601; defaults to the time the Worker receives the request). For Wallet/card automations, send the card label when available; Till can ask you to map an unrecognized card to an account. Leave `account` out if you want that card mapping to determine the account.

Available triggers, input variables, and the Shortcuts interface can differ between iOS versions and payment methods. Choose a trigger that provides the transaction details you need, and map its amount value to the JSON key `amount`. A manually run shortcut can use prompts for an amount and description instead. Test with a small expense and remove the test entry afterward.

## Export

```powershell
curl -H "Authorization: Bearer <SHORTCUT_TOKEN>" "https://till.<your-subdomain>.workers.dev/api/expenses?period=2026-10&format=csv"
```

`period` is `today`, `week`, `month` (default), `year` or `YYYY-MM`; omit `format=csv` for JSON.

## Development

```powershell
uv sync
uv run pytest                                       # unit tests (real SQL on sqlite3)

Copy-Item .dev.vars.example .dev.vars               # local-only secrets (not needed to deploy)
npx wrangler d1 migrations apply till --local
uv run pywrangler dev                               # http://localhost:8787
```

`.dev.vars` is a plain-text file for local development, not an encrypted vault. Keep it out of synced/shared folders if possible, use local/test credentials, and never commit it.

Schema changes: `npx wrangler d1 migrations create till <name>`, then apply `--local` / `--remote`. For an existing deployment, apply all new migrations before deploying code that depends on them:

```powershell
npx wrangler d1 migrations apply till --remote
uv run pywrangler deploy
```
