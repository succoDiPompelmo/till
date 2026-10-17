"""Register the deployed Worker as the Telegram bot webhook and set the bot's command menu.

Usage:
    uv run scripts/set_webhook.py https://till.<your-subdomain>.workers.dev

Reads TELEGRAM_BOT_TOKEN and TELEGRAM_WEBHOOK_SECRET from the environment, or from .dev.vars.
"""

import json
import os
import sys
import urllib.request
from pathlib import Path

COMMANDS = [
    ("today", "Today's totals by category"),
    ("week", "This week's totals"),
    ("month", "This month's totals (or /month YYYY-MM)"),
    ("year", "This year's totals"),
    ("last", "Latest expenses with ids"),
    ("undo", "Delete the last logged expense"),
    ("del", "Delete an expense: /del <id>"),
    ("cat", "Recategorize: /cat <id> <category>"),
    ("categories", "Categories in use"),
    ("help", "How to log expenses"),
]


def load_settings() -> dict[str, str]:
    values = {}
    dev_vars = Path(__file__).parent.parent / ".dev.vars"
    if dev_vars.exists():
        for line in dev_vars.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, _, value = line.partition("=")
                values[key.strip()] = value.strip().strip('"')
    values.update({k: v for k, v in os.environ.items() if k.startswith("TELEGRAM_")})
    return values


def call(token: str, method: str, payload: dict) -> dict:
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/{method}",
        data=json.dumps(payload).encode(),
        headers={"content-type": "application/json"},
    )
    with urllib.request.urlopen(req) as resp:
        return json.load(resp)


def main() -> None:
    if len(sys.argv) != 2 or not sys.argv[1].startswith("https://"):
        sys.exit(__doc__)
    settings = load_settings()
    token, secret = settings.get("TELEGRAM_BOT_TOKEN"), settings.get("TELEGRAM_WEBHOOK_SECRET")
    if not token or not secret:
        sys.exit("TELEGRAM_BOT_TOKEN and TELEGRAM_WEBHOOK_SECRET must be set")
    url = sys.argv[1].rstrip("/") + "/telegram"
    print(call(token, "setWebhook", {
        "url": url,
        "secret_token": secret,
        "allowed_updates": ["message", "callback_query"],
        "drop_pending_updates": True,
    }))
    print(call(token, "setMyCommands", {"commands": [{"command": c, "description": d} for c, d in COMMANDS]}))
    print(call(token, "getWebhookInfo", {}))


if __name__ == "__main__":
    main()
