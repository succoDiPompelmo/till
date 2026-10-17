from dataclasses import dataclass, field
from typing import Any


def _get(env: Any, name: str, default: str = "") -> str:
    value = env.get(name) if isinstance(env, dict) else getattr(env, name, None)
    return default if value is None else str(value).strip()


@dataclass
class Config:
    default_currency: str = "EUR"
    timezone: str = "UTC"
    telegram_bot_token: str = ""
    telegram_webhook_secret: str = ""
    allowed_chat_ids: set[int] = field(default_factory=set)
    shortcut_token: str = ""
    notify_chat_id: int | None = None

    @classmethod
    def from_env(cls, env: Any) -> "Config":
        allowed = {int(x) for x in _get(env, "ALLOWED_CHAT_IDS").replace(";", ",").split(",") if x.strip()}
        notify = _get(env, "NOTIFY_CHAT_ID")
        return cls(
            default_currency=_get(env, "DEFAULT_CURRENCY", "EUR").upper(),
            timezone=_get(env, "TIMEZONE", "UTC"),
            telegram_bot_token=_get(env, "TELEGRAM_BOT_TOKEN"),
            telegram_webhook_secret=_get(env, "TELEGRAM_WEBHOOK_SECRET"),
            allowed_chat_ids=allowed,
            shortcut_token=_get(env, "SHORTCUT_TOKEN"),
            notify_chat_id=int(notify) if notify else (min(allowed) if len(allowed) == 1 else None),
        )
