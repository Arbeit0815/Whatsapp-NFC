"""Zahlen, Beträge und Zeiten im deutschen Format (1.234,56 €)."""

from __future__ import annotations

import math
import re
import time

CURRENCY_SYMBOLS = {"EUR": "€", "USD": "$", "GBP": "£", "JPY": "¥"}

# Punkt und Komma gleichzeitig vertauschen: 1,234.56 -> 1.234,56
_SWAP = str.maketrans({",": ".", ".": ","})


def format_number(value: float, decimals: int = 2) -> str:
    """1234.5 -> '1.234,50' (Tausenderpunkt, Dezimalkomma)."""
    text = f"{value:,.{decimals}f}"
    if text.startswith("-") and not any(ch in "123456789" for ch in text):
        text = text[1:]  # kein "-0,00"
    return text.translate(_SWAP)


def _signed(text: str, value: float) -> str:
    if value > 0 and any(ch in "123456789" for ch in text):
        return "+" + text
    return text


def price_decimals(price: float) -> int:
    """Sinnvolle Nachkommastellen: 57.123,45 / 2,1234 / 0,00001234."""
    p = abs(price)
    if p >= 10 or p == 0:
        return 2
    if p >= 1:
        return 4
    return min(10, max(4, 3 - math.floor(math.log10(p))))


def currency_symbol(currency: str) -> str:
    code = (currency or "").upper()
    return CURRENCY_SYMBOLS.get(code, code)


def format_price(price: float | None, currency: str) -> str:
    if price is None:
        return "–"
    return f"{format_number(price, price_decimals(price))} {currency_symbol(currency)}".strip()


def format_money(value: float | None, currency: str, signed: bool = False) -> str:
    if value is None:
        return "–"
    text = format_number(value, 2)
    if signed:
        text = _signed(text, value)
    return f"{text} {currency_symbol(currency)}".strip()


def format_percent(value: float | None, signed: bool = True, decimals: int = 2) -> str:
    if value is None:
        return "–"
    text = format_number(value, decimals)
    if signed:
        text = _signed(text, value)
    return f"{text} %"


def format_plain(value: float, max_decimals: int = 4) -> str:
    """Zahl ohne überflüssige Nullen für Eingabefelder: 2,5 / 10 / 58.000."""
    text = format_number(value, max_decimals)
    if "," in text:
        text = text.rstrip("0").rstrip(",")
    return text


def format_amount(amount: float) -> str:
    """Menge ohne überflüssige Nullen: 0,05 / 1.250 / 0,00012345."""
    text = f"{amount:,.8f}".rstrip("0").rstrip(".")
    return text.translate(_SWAP)


def arrow(value: float | None) -> str:
    if value is None or value == 0:
        return ""
    return "▲" if value > 0 else "▼"


def format_change(value: float | None) -> str:
    """Prozentwert mit Pfeil für Tabellen: '▲ +2,35 %'."""
    if value is None:
        return "–"
    return f"{arrow(value)} {format_percent(value)}".strip()


def window_label(minutes: int) -> str:
    """5 -> '5 Min', 60 -> '1 Std', 1440 -> '24 Std', 10080 -> '7 Tage'."""
    if minutes < 60:
        return f"{minutes} Min"
    if minutes % 1440 == 0 and minutes > 1440:
        return f"{minutes // 1440} Tage"
    if minutes % 60 == 0:
        return f"{minutes // 60} Std"
    return f"{minutes} Min"


def format_time(timestamp: float) -> str:
    return time.strftime("%H:%M:%S", time.localtime(timestamp))


def format_datetime(timestamp: float) -> str:
    return time.strftime("%d.%m.%Y %H:%M:%S", time.localtime(timestamp))


_GROUPED_DOTS = re.compile(r"^\d{1,3}(\.\d{3})+$")


def parse_number(text: str) -> float | None:
    """Liest Zahlen so, wie man sie in Deutschland eintippt.

    '1.234,56' -> 1234.56, '0,5' -> 0.5, '58.000' -> 58000, '0.123' -> 0.123.
    Leere Eingabe -> None. Ungültige Eingabe -> ValueError.
    """
    cleaned = (text or "").strip()
    for token in ("€", "$", "%", " ", " ", "'"):
        cleaned = cleaned.replace(token, "")
    cleaned = cleaned.lstrip("+")
    if not cleaned:
        return None
    if "," in cleaned and "." in cleaned:
        # Das zuletzt stehende Zeichen ist das Dezimaltrennzeichen
        if cleaned.rfind(",") > cleaned.rfind("."):
            cleaned = cleaned.replace(".", "").replace(",", ".")
        else:
            cleaned = cleaned.replace(",", "")
    elif "," in cleaned:
        if cleaned.count(",") > 1:
            raise ValueError(text)
        cleaned = cleaned.replace(",", ".")
    elif cleaned.count(".") > 1 or (_GROUPED_DOTS.match(cleaned) and not cleaned.startswith("0")):
        # 58.000 oder 1.250.000 -> Tausenderpunkte
        if not _GROUPED_DOTS.match(cleaned):
            raise ValueError(text)
        cleaned = cleaned.replace(".", "")
    value = float(cleaned)
    if math.isnan(value) or math.isinf(value):
        raise ValueError(text)
    return value
