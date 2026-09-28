"""Gemeinsame Hilfen für die Tests."""

import copy
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Warnungen des Programms nicht in die Testausgabe schreiben
logging.getLogger("krypto_waechter").addHandler(logging.NullHandler())

from krypto_waechter.config import normalize_config  # noqa: E402
from krypto_waechter.providers import Quote  # noqa: E402


def make_config(**overrides) -> dict:
    """Standardeinstellungen mit Änderungen, z. B. make_config(coins=[...])."""
    raw = copy.deepcopy(normalize_config({}))
    raw.update(overrides)
    return normalize_config(raw)


def quote(coin_id="bitcoin", price=100.0, source="coingecko", currency="EUR", h1=None, h24=None, d7=None,
          rank=None, name=None, symbol=None) -> Quote:
    return Quote(
        key=f"{source}:{coin_id}", source=source, coin_id=coin_id, symbol=symbol or coin_id[:3].upper(),
        name=name or coin_id.title(), price=price, currency=currency,
        changes={"1h": h1, "24h": h24, "7d": d7}, rank=rank,
    )


class FakeRequest:
    """Ersetzt request_json: liefert vorbereitete Antworten und merkt sich die Aufrufe."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, url, params=None, headers=None, data=None, timeout=20.0):
        self.calls.append({"url": url, "params": params, "headers": headers, "data": data})
        response = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        if isinstance(response, Exception):
            raise response
        if callable(response):
            return response(url, params)
        return copy.deepcopy(response)
