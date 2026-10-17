import pytest

from till.bot import parse_expense_text
from till.money import AmountError, format_money, parse_amount


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("12.50", (1250, "EUR")),
        ("12,50", (1250, "EUR")),
        ("€12.50", (1250, "EUR")),
        ("12,50 €", (1250, "EUR")),
        ("$1,234.56", (123456, "USD")),
        ("1.234,56 €", (123456, "EUR")),
        ("1,234", (123400, "EUR")),
        ("CHF 7.20", (720, "CHF")),
        ("£3", (300, "GBP")),
        ("-15", (-1500, "EUR")),
        ("-€4,99", (-499, "EUR")),
        ("1\u202f234,00\u00a0€", (123400, "EUR")),
        (9.99, (999, "EUR")),
        (12, (1200, "EUR")),
    ],
)
def test_parse_amount(raw, expected):
    assert parse_amount(raw, "EUR") == expected


@pytest.mark.parametrize("raw", ["", "abc", "0", "€", True])
def test_parse_amount_rejects(raw):
    with pytest.raises(AmountError):
        parse_amount(raw, "EUR")


def test_format_money():
    assert format_money(123456, "EUR") == "€1,234.56"
    assert format_money(-5, "USD") == "-$0.05"
    assert format_money(1000, "CHF") == "10.00 CHF"


@pytest.mark.parametrize(
    "text, amount, currency, description, category",
    [
        ("12.50 lunch with Marco #food", 1250, "EUR", "lunch with Marco", "food"),
        ("8,90 taxi", 890, "EUR", "taxi", None),
        ("taxi 8,90", 890, "EUR", "taxi", None),
        ("20 USD souvenir", 2000, "USD", "souvenir", None),
        ("20 usd souvenir", 2000, "USD", "souvenir", None),
        ("5 tea", 500, "EUR", "tea", None),
        ("€30 #Gifts", 3000, "EUR", None, "gifts"),
    ],
)
def test_parse_expense_text(text, amount, currency, description, category):
    parsed = parse_expense_text(text, "EUR")
    assert (parsed.amount_cents, parsed.currency, parsed.description, parsed.category, parsed.account) == (
        amount, currency, description, category, None,
    )


def test_parse_expense_with_account():
    parsed = parse_expense_text("12.50 lunch #food @revolut", "EUR")
    assert (parsed.description, parsed.category, parsed.account) == ("lunch", "food", "revolut")


@pytest.mark.parametrize("text", ["hello there", "", "lunch"])
def test_parse_expense_text_none(text):
    assert parse_expense_text(text, "EUR") is None
