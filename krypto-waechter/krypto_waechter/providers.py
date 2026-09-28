"""Kursquellen: CoinGecko (Marktdaten über alle Börsen), Binance (Börsenkurse) und Demo-Kurse."""

from __future__ import annotations

import json
import logging
import math
import random
import time
from dataclasses import dataclass, field

from .formatting import format_price
from .net import NetError, RateLimitError, request_json

log = logging.getLogger(__name__)

SOURCE_LABELS = {"coingecko": "CoinGecko", "binance": "Binance"}


@dataclass
class Quote:
    """Aktueller Kurs eines Coins."""

    key: str  # z. B. "coingecko:bitcoin" oder "binance:BTCEUR"
    source: str
    coin_id: str
    symbol: str
    name: str
    price: float
    currency: str
    # Veränderung in Prozent laut Datenquelle: {"1h": …, "24h": …, "7d": …}
    changes: dict = field(default_factory=dict)
    rank: int | None = None
    fetched_at: float = 0.0

    @property
    def label(self) -> str:
        if self.source == "binance":
            return f"{self.symbol}/{self.currency} (Binance)"
        if self.symbol and self.name and self.symbol.upper() != self.name.upper():
            return f"{self.name} ({self.symbol})"
        return self.name or self.symbol or self.coin_id


def _float(value) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _first_float(item: dict, *names: str) -> float | None:
    for name in names:
        number = _float(item.get(name))
        if number is not None:
            return number
    return None


# ---------------------------------------------------------------------------
# CoinGecko
# ---------------------------------------------------------------------------

def parse_coingecko_market(item, currency: str, now: float) -> Quote | None:
    """Wandelt einen Eintrag von /coins/markets in ein Quote-Objekt um."""
    if not isinstance(item, dict) or not item.get("id"):
        return None
    price = _float(item.get("current_price"))
    if price is None or price <= 0:
        return None
    coin_id = str(item["id"])
    rank = item.get("market_cap_rank")
    return Quote(
        key=f"coingecko:{coin_id}",
        source="coingecko",
        coin_id=coin_id,
        symbol=str(item.get("symbol") or "").upper(),
        name=str(item.get("name") or coin_id),
        price=price,
        currency=currency.upper(),
        changes={
            "1h": _first_float(item, "price_change_percentage_1h_in_currency"),
            "24h": _first_float(item, "price_change_percentage_24h_in_currency", "price_change_percentage_24h"),
            "7d": _first_float(item, "price_change_percentage_7d_in_currency"),
        },
        rank=int(rank) if isinstance(rank, (int, float)) and not isinstance(rank, bool) else None,
        fetched_at=now,
    )


class CoinGecko:
    """Öffentliche CoinGecko-API (ohne Anmeldung; optional mit kostenlosem Demo-API-Key)."""

    source = "coingecko"
    BASE_URL = "https://api.coingecko.com/api/v3"
    MAX_PER_PAGE = 250

    def __init__(self, currency: str = "eur", api_key: str = "", request=request_json):
        self.currency = currency.lower()
        self.api_key = api_key.strip()
        self._request = request

    def _get(self, path: str, params: dict | None = None):
        headers = {"x-cg-demo-api-key": self.api_key} if self.api_key else None
        try:
            return self._request(self.BASE_URL + path, params=params, headers=headers)
        except RateLimitError as exc:
            raise RateLimitError(f"CoinGecko: {exc}", status=exc.status, code=exc.code) from None
        except NetError as exc:
            message = f"CoinGecko: {exc}"
            if exc.status in (401, 403) and self.api_key:
                message += " – bitte den API-Key in den Einstellungen prüfen"
            raise NetError(message, status=exc.status, code=exc.code) from None

    def _markets(self, params: dict) -> list[Quote]:
        query = {
            "vs_currency": self.currency,
            "order": "market_cap_desc",
            "sparkline": "false",
            "price_change_percentage": "1h,24h,7d",
        }
        query.update(params)
        data = self._get("/coins/markets", query)
        if not isinstance(data, list):
            raise NetError("CoinGecko: unerwartete Antwort")
        now = time.time()
        return [q for q in (parse_coingecko_market(item, self.currency, now) for item in data) if q]

    def quotes(self, ids: list[str]) -> dict[str, Quote]:
        """Kurse für bestimmte Coins (CoinGecko-IDs wie 'bitcoin')."""
        ids = list(dict.fromkeys(ids))
        result = {}
        for start in range(0, len(ids), self.MAX_PER_PAGE):
            chunk = ids[start:start + self.MAX_PER_PAGE]
            for quote in self._markets({"ids": ",".join(chunk), "per_page": len(chunk), "page": 1}):
                result[quote.key] = quote
        return result

    def top(self, count: int) -> list[Quote]:
        """Die größten Coins nach Marktkapitalisierung."""
        count = max(1, min(self.MAX_PER_PAGE, int(count)))
        return self._markets({"per_page": count, "page": 1})

    def search(self, query: str) -> list[dict]:
        data = self._get("/search", {"query": query})
        coins = data.get("coins") if isinstance(data, dict) else None
        results = []
        for coin in coins or []:
            if isinstance(coin, dict) and coin.get("id"):
                rank = coin.get("market_cap_rank")
                results.append({
                    "source": "coingecko",
                    "id": str(coin["id"]),
                    "name": str(coin.get("name") or coin["id"]),
                    "symbol": str(coin.get("symbol") or "").upper(),
                    "info": f"Rang {rank}" if isinstance(rank, int) else "",
                })
        return results[:50]


# ---------------------------------------------------------------------------
# Binance
# ---------------------------------------------------------------------------

QUOTE_ASSETS = (
    "FDUSD", "USDT", "USDC", "TUSD", "BUSD", "USDP", "DAI", "EUR", "TRY", "BRL", "GBP", "AUD", "JPY",
    "PLN", "RON", "ZAR", "UAH", "ARS", "MXN", "COP", "CZK", "IDR", "BTC", "ETH", "BNB", "XRP", "TRX",
    "DOGE", "SOL", "USD",
)
PREFERRED_QUOTES = ("EUR", "USDT", "USDC", "FDUSD", "BTC", "ETH", "BNB")


def split_symbol(symbol: str) -> tuple[str, str]:
    """'BTCEUR' -> ('BTC', 'EUR'), 'ETHUSDT' -> ('ETH', 'USDT')."""
    for quote in sorted(QUOTE_ASSETS, key=len, reverse=True):
        if symbol.endswith(quote) and len(symbol) > len(quote):
            return symbol[: -len(quote)], quote
    return symbol, ""


def parse_binance_ticker(item, now: float) -> Quote | None:
    """Wandelt einen Eintrag von /api/v3/ticker/24hr in ein Quote-Objekt um."""
    if not isinstance(item, dict) or not item.get("symbol"):
        return None
    price = _float(item.get("lastPrice"))
    if price is None or price <= 0:
        return None
    symbol = str(item["symbol"]).upper()
    base, quote = split_symbol(symbol)
    return Quote(
        key=f"binance:{symbol}",
        source="binance",
        coin_id=symbol,
        symbol=base,
        name=f"{base}/{quote}" if quote else symbol,
        price=price,
        currency=quote,
        changes={"1h": None, "24h": _float(item.get("priceChangePercent")), "7d": None},
        fetched_at=now,
    )


def _window_change(item: dict) -> float | None:
    """Prozentuale Veränderung aus einem Rolling-Window-Ticker."""
    percent = _float(item.get("priceChangePercent"))
    if percent is not None:
        return percent
    open_price, last_price = _float(item.get("openPrice")), _float(item.get("lastPrice"))
    if open_price and last_price is not None:
        return (last_price / open_price - 1) * 100
    return None


class Binance:
    """Öffentliche Marktdaten-API von Binance (ohne Konto und ohne API-Key)."""

    source = "binance"
    BASE_URLS = ("https://api.binance.com", "https://data-api.binance.vision")
    CHUNK = 100

    def __init__(self, request=request_json):
        self._request = request
        self._base = 0
        self.invalid_symbols: set[str] = set()

    def _get(self, path: str, params: dict | None = None):
        last_error = None
        for attempt in range(len(self.BASE_URLS)):
            index = (self._base + attempt) % len(self.BASE_URLS)
            try:
                data = self._request(self.BASE_URLS[index] + path, params=params)
            except RateLimitError as exc:
                raise RateLimitError(f"Binance: {exc}", status=exc.status, code=exc.code) from None
            except NetError as exc:
                if exc.status == 400:
                    raise  # fehlerhafte Anfrage, z. B. unbekanntes Symbol – Ausweichserver hilft nicht
                last_error = exc
                continue
            self._base = index
            return data
        raise NetError(f"Binance: {last_error}", status=last_error.status, code=last_error.code)

    def _tickers(self, path: str, symbols: list[str], **extra) -> list[dict]:
        items = []
        for start in range(0, len(symbols), self.CHUNK):
            params = {"symbols": json.dumps(symbols[start:start + self.CHUNK], separators=(",", ":"))}
            params.update(extra)
            data = self._get(path, params)
            if isinstance(data, dict):
                data = [data]
            if not isinstance(data, list):
                raise NetError("Binance: unerwartete Antwort")
            items.extend(entry for entry in data if isinstance(entry, dict))
        return items

    def _tickers_checked(self, path: str, symbols: list[str]) -> list[dict]:
        """Wie _tickers, sortiert aber unbekannte Symbole aus (Binance lehnt sonst alles ab)."""
        try:
            return self._tickers(path, symbols)
        except NetError as exc:
            if exc.status != 400:
                raise
        items = []
        for symbol in symbols:
            try:
                items.extend(self._tickers(path, [symbol]))
            except NetError as exc:
                if exc.status != 400:
                    raise
                self.invalid_symbols.add(symbol)
                log.warning("Binance kennt das Symbol %s nicht (%s)", symbol, exc)
        return items

    def quotes(self, symbols: list[str]) -> dict[str, Quote]:
        """Kurse für Handelspaare wie 'BTCEUR' oder 'ETHUSDT'."""
        wanted = [s.upper() for s in dict.fromkeys(symbols) if s.upper() not in self.invalid_symbols]
        if not wanted:
            return {}
        now = time.time()
        by_symbol = {}
        for item in self._tickers_checked("/api/v3/ticker/24hr", wanted):
            quote = parse_binance_ticker(item, now)
            if quote:
                by_symbol[quote.coin_id] = quote
        # 1-Std.- und 7-Tage-Veränderung über rollierende Zeitfenster (optional)
        for label in ("1h", "7d"):
            if not by_symbol:
                break
            try:
                for item in self._tickers("/api/v3/ticker", list(by_symbol), windowSize=label):
                    quote = by_symbol.get(str(item.get("symbol", "")).upper())
                    if quote:
                        quote.changes[label] = _window_change(item)
            except NetError as exc:
                log.info("Binance-Zeitfenster %s nicht verfügbar: %s", label, exc)
        return {quote.key: quote for quote in by_symbol.values()}

    def search(self, query: str) -> list[dict]:
        text = query.strip().upper().replace("/", "").replace("-", "").replace(" ", "")
        data = self._get("/api/v3/ticker/price")
        if isinstance(data, dict):
            data = [data]
        matches = []
        for item in data if isinstance(data, list) else []:
            symbol = str(item.get("symbol", "")).upper() if isinstance(item, dict) else ""
            price = _float(item.get("price")) if symbol else None
            if text and text in symbol and price:
                base, quote = split_symbol(symbol)
                exact = 0 if base == text or symbol == text else (1 if symbol.startswith(text) else 2)
                preferred = PREFERRED_QUOTES.index(quote) if quote in PREFERRED_QUOTES else len(PREFERRED_QUOTES)
                matches.append(((exact, preferred, symbol), {
                    "source": "binance",
                    "id": symbol,
                    "name": f"{base}/{quote}" if quote else symbol,
                    "symbol": base,
                    "info": format_price(price, quote),
                }))
        matches.sort(key=lambda entry: entry[0])
        return [entry for _, entry in matches[:60]]


# ---------------------------------------------------------------------------
# Demo-Modus (simulierte Kurse, kein Internet nötig)
# ---------------------------------------------------------------------------

DEMO_COINS = [
    ("bitcoin", "Bitcoin", "BTC", 58000.0),
    ("ethereum", "Ethereum", "ETH", 2300.0),
    ("tether", "Tether", "USDT", 0.92),
    ("binancecoin", "BNB", "BNB", 520.0),
    ("solana", "Solana", "SOL", 130.0),
    ("ripple", "XRP", "XRP", 0.52),
    ("usd-coin", "USDC", "USDC", 0.92),
    ("dogecoin", "Dogecoin", "DOGE", 0.105),
    ("cardano", "Cardano", "ADA", 0.33),
    ("tron", "TRON", "TRX", 0.14),
    ("avalanche-2", "Avalanche", "AVAX", 24.0),
    ("shiba-inu", "Shiba Inu", "SHIB", 0.0000152),
    ("chainlink", "Chainlink", "LINK", 11.5),
    ("polkadot", "Polkadot", "DOT", 4.1),
    ("sui", "Sui", "SUI", 1.6),
    ("litecoin", "Litecoin", "LTC", 64.0),
    ("pepe", "Pepe", "PEPE", 0.0000081),
    ("near", "NEAR Protocol", "NEAR", 4.3),
    ("uniswap", "Uniswap", "UNI", 6.9),
    ("aptos", "Aptos", "APT", 6.2),
    ("stellar", "Stellar", "XLM", 0.09),
    ("render-token", "Render", "RENDER", 5.1),
    ("bonk", "Bonk", "BONK", 0.000019),
    ("injective-protocol", "Injective", "INJ", 17.0),
    ("floki", "FLOKI", "FLOKI", 0.00012),
]


class DemoMarket:
    """Erzeugt simulierte Kurse: Zufallsbewegungen mit gelegentlichen Sprüngen.

    Jeder Abruf entspricht fünf simulierten Minuten. Einige Sprünge sind fest
    eingeplant, damit man die Alarme schon nach wenigen Abrufen sieht.
    """

    STEPS_PER_HOUR = 12
    # Abruf-Nr. -> (Position in der Watchlist, Sprung)
    WATCH_EVENTS = {3: (1, 0.07), 6: (2, -0.09), 9: (0, 0.06)}
    SCANNER_EVENTS = {4: ("pepe", 0.16), 8: ("bonk", -0.14)}

    def __init__(self, seed: int | None = None):
        self.rng = random.Random(seed)
        self.step = 0
        self.paths: dict[str, list[float]] = {}
        self.meta: dict[str, tuple] = {}
        self._known = {coin_id: (name, symbol, price) for coin_id, name, symbol, price in DEMO_COINS}

    def _base(self, coin: dict) -> tuple[str, str, float]:
        if coin["source"] == "binance":
            base, quote = split_symbol(coin["id"])
            for name, symbol, price in self._known.values():
                if symbol == base:
                    return f"{base}/{quote}", base, price
            return f"{base}/{quote}", base, 1.0
        name, symbol, price = self._known.get(coin["id"], (coin.get("name") or coin["id"], coin.get("symbol") or "", 1.0))
        return name, symbol, price

    def _ensure(self, key: str, name: str, symbol: str, price: float) -> None:
        if key not in self.paths:
            self.paths[key] = [price * self.rng.uniform(0.97, 1.03)]
            self.meta[key] = (name, symbol, self.rng.uniform(-6, 6), self.rng.uniform(-15, 15))

    def tick(self, coins: list[dict]) -> None:
        """Einen Zeitschritt weiter simulieren."""
        for coin in coins:
            self._ensure(f"{coin['source']}:{coin['id']}", *self._base(coin))
        for coin_id, name, symbol, price in DEMO_COINS:
            self._ensure(f"coingecko:{coin_id}", name, symbol, price)
        self.step += 1
        for path in self.paths.values():
            move = self.rng.gauss(0, 0.004)
            if self.rng.random() < 0.015:
                move += self.rng.choice((-1, 1)) * self.rng.uniform(0.04, 0.08)
            path.append(path[-1] * math.exp(move))
            del path[:-400]
        watch_keys = [f"{c['source']}:{c['id']}" for c in coins]
        if self.step in self.WATCH_EVENTS:
            index, jump = self.WATCH_EVENTS[self.step]
            if index < len(watch_keys):
                self.paths[watch_keys[index]][-1] *= 1 + jump
        if self.step in self.SCANNER_EVENTS:
            coin_id, jump = self.SCANNER_EVENTS[self.step]
            self.paths[f"coingecko:{coin_id}"][-1] *= 1 + jump

    def _quote(self, key: str, source: str, coin_id: str, currency: str, rank: int | None, now: float) -> Quote:
        path = self.paths[key]
        name, symbol, base_24h, base_7d = self.meta[key]
        hour_ago = path[-1 - self.STEPS_PER_HOUR] if len(path) > self.STEPS_PER_HOUR else path[0]
        drift = path[-1] / path[0]
        changes = {
            "1h": (path[-1] / hour_ago - 1) * 100,
            "24h": ((1 + base_24h / 100) * drift - 1) * 100,
            "7d": ((1 + base_7d / 100) * drift - 1) * 100,
        }
        return Quote(key=key, source=source, coin_id=coin_id, symbol=symbol, name=name, price=path[-1],
                     currency=currency.upper(), changes=changes, rank=rank, fetched_at=now)

    def quotes(self, coins: list[dict], currency: str) -> dict[str, Quote]:
        now = time.time()
        result = {}
        for coin in coins:
            key = f"{coin['source']}:{coin['id']}"
            self._ensure(key, *self._base(coin))
            quote_currency = split_symbol(coin["id"])[1] if coin["source"] == "binance" else currency
            result[key] = self._quote(key, coin["source"], coin["id"], quote_currency or currency, None, now)
        return result

    def top(self, count: int, currency: str) -> list[Quote]:
        now = time.time()
        quotes = []
        for rank, (coin_id, name, symbol, price) in enumerate(DEMO_COINS[:count], start=1):
            key = f"coingecko:{coin_id}"
            self._ensure(key, name, symbol, price)
            quotes.append(self._quote(key, "coingecko", coin_id, currency, rank, now))
        return quotes

    def search(self, source: str, query: str) -> list[dict]:
        text = query.strip().lower()
        results = []
        for rank, (coin_id, name, symbol, price) in enumerate(DEMO_COINS, start=1):
            if text in coin_id or text in name.lower() or text == symbol.lower():
                if source == "binance":
                    results.append({"source": "binance", "id": f"{symbol}EUR", "name": f"{symbol}/EUR",
                                     "symbol": symbol, "info": format_price(price, "EUR")})
                else:
                    results.append({"source": "coingecko", "id": coin_id, "name": name, "symbol": symbol,
                                    "info": f"Rang {rank}"})
        return results
