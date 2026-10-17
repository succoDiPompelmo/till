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
| `/today` `/week` `/month` `/year` `/month 2026-09` | Totals by category |
| `/last [n]` | Latest expenses with their ids |
| `/undo`, `/del <id>` | Delete |
| `/cat <id> <category>` | Recategorize (and remember the merchant/description) |

## iPhone: automatic logging of Apple Pay payments

Requires iOS 17+. In the **Shortcuts** app:

1. **Automation** tab → **+** → **Transaction** (Wallet).
2. Choose the cards (and categories) to track, select **Run Immediately**, then **Next** → **New Blank Automation**.
3. Add the action **Get Contents of URL**:
   - URL: `https://till.<your-subdomain>.workers.dev/api/expenses`
   - Method: **POST**
   - Headers: `Authorization` = `Bearer <SHORTCUT_TOKEN>`
   - Request Body: **JSON** with these fields (tap each value and pick the *Shortcut Input* variable, then the property):
     | Key | Type | Value |
     | --- | --- | --- |
     | `amount` | Text | Shortcut Input → **Amount** |
     | `merchant` | Text | Shortcut Input → **Merchant** |
     | `card` | Text | Shortcut Input → **Card or Pass** |
4. Done. Pay with Apple Pay → the expense is stored and you get a Telegram message to pick a category (only the first time for each merchant).

> The Wallet trigger only fires for Apple Pay/Wallet payments. Anything else → Telegram.

The amount can be any format the Wallet produces (`€12,40`, `12,40 €`, `$1,234.56`). Other optional JSON fields:
`currency` (ISO code), `category`, `name` (description), `date` (ISO 8601, defaults to now).

**Bonus — manual shortcut / Siri:** create a regular shortcut with *Ask for Input (Number)* → *Ask for Input (Text)* → the same *Get Contents of URL* using `amount` and `name`. Add it to the Home Screen or call it with "Hey Siri, log expense".

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

Schema changes: `npx wrangler d1 migrations create till <name>`, then apply `--local` / `--remote`.
