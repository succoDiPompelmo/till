"""Amount parsing and formatting. Amounts are always stored as integer cents."""

import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

SYMBOLS = {
    "€": "EUR",
    "$": "USD",
    "£": "GBP",
    "¥": "JPY",
    "₣": "CHF",
    "₹": "INR",
    "₺": "TRY",
    "₽": "RUB",
    "zł": "PLN",
    "kč": "CZK",
    "fr.": "CHF",
}
CURRENCY_SYMBOLS = {"EUR": "€", "USD": "$", "GBP": "£", "JPY": "¥", "INR": "₹"}
KNOWN_CODES = {
    "EUR", "USD", "GBP", "CHF", "JPY", "CAD", "AUD", "NZD", "SEK", "NOK", "DKK",
    "PLN", "CZK", "HUF", "RON", "BGN", "TRY", "INR", "CNY", "HKD", "SGD", "THB",
    "MXN", "BRL", "ZAR", "AED", "ILS", "KRW", "RUB",
}


def as_currency_code(token: str) -> str | None:
    """Return the ISO code if token looks like a currency code ('usd', 'EUR', 'XYZ')."""
    if len(token) == 3 and token.isalpha() and (token.upper() in KNOWN_CODES or token.isupper()):
        return token.upper()
    return None

_CODE_RE = re.compile(r"\b([A-Za-z]{3})\b")
_NUMBER_RE = re.compile(r"[-+]?\d[\d.,'\s\u00a0\u202f]*")


class AmountError(ValueError):
    pass


def _normalize_number(raw: str) -> Decimal:
    s = re.sub(r"[\s\u00a0\u202f']", "", raw).rstrip(".,")
    if "," in s and "." in s:
        # whichever separator comes last is the decimal separator
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        head, _, tail = s.rpartition(",")
        if s.count(",") == 1 and len(tail) <= 2:
            s = f"{head}.{tail}"
        else:
            s = s.replace(",", "")
    elif s.count(".") > 1:
        s = s.replace(".", "")
    try:
        return Decimal(s)
    except InvalidOperation as e:
        raise AmountError(f"Not a valid amount: {raw!r}") from e


def parse_amount(
    value: str | int | float, default_currency: str, *, allow_zero: bool = False
) -> tuple[int, str]:
    """Parse '12.50', '€12,50', '12,50 €', '$1,234.56', 'USD 10' or a number.

    Returns (cents, ISO currency code). Negative amounts (refunds) are kept negative.
    """
    if isinstance(value, bool):
        raise AmountError("Not a valid amount")
    if isinstance(value, (int, float)):
        number, currency = Decimal(str(value)), default_currency
    else:
        text = str(value).strip()
        if not text:
            raise AmountError("Empty amount")
        currency = None
        lowered = text.lower()
        for symbol, code in SYMBOLS.items():
            if symbol in lowered:
                currency = code
                break
        if currency is None:
            for m in _CODE_RE.finditer(text):
                if currency := as_currency_code(m.group(1)):
                    break
        m = _NUMBER_RE.search(text)
        if not m:
            raise AmountError(f"Not a valid amount: {text!r}")
        number = _normalize_number(m.group(0))
        if text.lstrip().startswith("-") or "-" in text[: m.start()]:
            number = -abs(number)
        currency = currency or default_currency
    cents = int((number * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    if cents == 0 and not allow_zero:
        raise AmountError("Amount must not be zero")
    return cents, currency.upper()


def format_money(cents: int, currency: str) -> str:
    sign = "-" if cents < 0 else ""
    value = f"{abs(cents) // 100:,}.{abs(cents) % 100:02d}"
    symbol = CURRENCY_SYMBOLS.get(currency)
    return f"{sign}{symbol}{value}" if symbol else f"{sign}{value} {currency}"
