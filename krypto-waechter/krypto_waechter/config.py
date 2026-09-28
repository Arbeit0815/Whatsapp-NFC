"""Einstellungen laden, prüfen und speichern (config.json)."""

from __future__ import annotations

import copy
import json
import logging
import math
import os
import re
import shutil
import sys
import threading
from pathlib import Path

log = logging.getLogger(__name__)

SOURCES = ("coingecko", "binance")

# Währungen, die CoinGecko als Kurswährung anbietet (Auswahl)
CURRENCIES = ("eur", "usd", "chf", "gbp", "jpy", "cad", "aud", "pln", "sek", "nok", "dkk", "try", "btc", "eth")

DEFAULT_CONFIG = {
    "currency": "eur",
    "poll_interval": 60,
    "coingecko_api_key": "",
    "coins": [
        {"source": "coingecko", "id": "bitcoin", "name": "Bitcoin", "symbol": "BTC"},
        {"source": "coingecko", "id": "ethereum", "name": "Ethereum", "symbol": "ETH"},
        {"source": "coingecko", "id": "solana", "name": "Solana", "symbol": "SOL"},
        {"source": "coingecko", "id": "ripple", "name": "XRP", "symbol": "XRP"},
        {"source": "coingecko", "id": "cardano", "name": "Cardano", "symbol": "ADA"},
        {"source": "coingecko", "id": "dogecoin", "name": "Dogecoin", "symbol": "DOGE"},
    ],
    # Alarm, wenn sich ein Coin innerhalb von <minutes> um mindestens <percent> % bewegt
    "move_rules": [
        {"minutes": 5, "percent": 2.0, "enabled": False},
        {"minutes": 15, "percent": 3.0, "enabled": True},
        {"minutes": 60, "percent": 5.0, "enabled": True},
        {"minutes": 240, "percent": 7.0, "enabled": False},
        {"minutes": 1440, "percent": 10.0, "enabled": True},
    ],
    # Frühestens nach dieser Zeit erneut melden (bei weiterer starker Bewegung sofort)
    "cooldown_minutes": 30,
    # Markt-Scanner: die größten Coins nach Marktkapitalisierung (CoinGecko)
    "scanner": {"enabled": True, "top_n": 100, "percent_1h": 10.0, "percent_24h": 25.0},
    # Portfolio: Gewinn/Verlust seit Kauf und Bewegung des Gesamtwerts (0 = aus)
    "portfolio": {"profit_percent": 25.0, "loss_percent": 15.0, "move_percent": 5.0, "move_minutes": 60},
    "notifications": {
        "toast": True,
        "toast_compat": False,
        "sound": True,
        "flash": True,
        "telegram_enabled": False,
        "telegram_token": "",
        "telegram_chat_id": "",
    },
    "autostart": False,
    "window_geometry": "",
}

_GEOMETRY = re.compile(r"^\d{2,5}x\d{2,5}([+-]-?\d{1,5}[+-]-?\d{1,5})?$")
_CURRENCY = re.compile(r"^[a-z]{3,5}$")


def coin_key(coin: dict) -> str:
    """Eindeutiger Schlüssel eines Coins, z. B. 'coingecko:bitcoin' oder 'binance:BTCEUR'."""
    return f"{coin['source']}:{coin['id']}"


def coin_label(coin: dict) -> str:
    """Anzeigename eines Coins, solange noch keine Kursdaten vorliegen."""
    name, symbol = coin.get("name") or "", coin.get("symbol") or ""
    if coin["source"] == "binance":
        return f"{coin['id']} (Binance)"
    if name and symbol and name.upper() != symbol.upper():
        return f"{name} ({symbol})"
    return name or symbol or coin["id"]


def default_config_dir() -> Path:
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base) / "KryptoWaechter"
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "krypto-waechter"


def _num(value, default: float, minimum: float | None = None, maximum: float | None = None) -> float:
    if isinstance(value, bool):
        return default
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if math.isnan(number) or math.isinf(number):
        return default
    if minimum is not None:
        number = max(minimum, number)
    if maximum is not None:
        number = min(maximum, number)
    return number


def _optional_price(value) -> float | None:
    number = _num(value, 0.0)
    return number if number > 0 else None


def _text(value) -> str:
    return str(value).strip() if value is not None else ""


def normalize_coin(raw) -> dict | None:
    if not isinstance(raw, dict):
        return None
    source = _text(raw.get("source")).lower()
    coin_id = _text(raw.get("id"))
    if source not in SOURCES or not coin_id:
        return None
    # CoinGecko-IDs sind klein geschrieben, Binance-Symbole groß
    coin_id = coin_id.lower() if source == "coingecko" else coin_id.upper().replace("/", "").replace("-", "")
    return {
        "source": source,
        "id": coin_id,
        "name": _text(raw.get("name")),
        "symbol": _text(raw.get("symbol")).upper(),
        "amount": _num(raw.get("amount"), 0.0, minimum=0.0),
        "buy_price": _num(raw.get("buy_price"), 0.0, minimum=0.0),
        "alarm_above": _optional_price(raw.get("alarm_above")),
        "alarm_below": _optional_price(raw.get("alarm_below")),
    }


def normalize_config(raw) -> dict:
    """Ergänzt fehlende Werte, korrigiert ungültige und entfernt Unbekanntes."""
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    cfg["coins"] = [normalize_coin(c) for c in cfg["coins"]]
    if not isinstance(raw, dict):
        return cfg

    currency = _text(raw.get("currency")).lower()
    if _CURRENCY.match(currency):
        cfg["currency"] = currency
    cfg["poll_interval"] = int(_num(raw.get("poll_interval"), 60, 20, 3600))
    cfg["coingecko_api_key"] = _text(raw.get("coingecko_api_key"))

    if isinstance(raw.get("coins"), list):
        coins, seen = [], set()
        for item in raw["coins"]:
            coin = normalize_coin(item)
            if coin and coin_key(coin) not in seen:
                seen.add(coin_key(coin))
                coins.append(coin)
        cfg["coins"] = coins

    if isinstance(raw.get("move_rules"), list):
        rules = {}
        for rule in raw["move_rules"]:
            if not isinstance(rule, dict):
                continue
            minutes = int(_num(rule.get("minutes"), 0, 0, 10080))
            if minutes >= 1:
                rules[minutes] = {
                    "minutes": minutes,
                    "percent": round(_num(rule.get("percent"), 5.0, 0.1, 1000.0), 4),
                    "enabled": bool(rule.get("enabled", True)),
                }
        cfg["move_rules"] = [rules[m] for m in sorted(rules)]

    cfg["cooldown_minutes"] = int(_num(raw.get("cooldown_minutes"), 30, 0, 1440))

    scanner = raw.get("scanner")
    if isinstance(scanner, dict):
        cfg["scanner"] = {
            "enabled": bool(scanner.get("enabled", True)),
            "top_n": int(_num(scanner.get("top_n"), 100, 10, 250)),
            "percent_1h": _num(scanner.get("percent_1h"), 10.0, 0.0, 1000.0),
            "percent_24h": _num(scanner.get("percent_24h"), 25.0, 0.0, 1000.0),
        }

    portfolio = raw.get("portfolio")
    if isinstance(portfolio, dict):
        cfg["portfolio"] = {
            "profit_percent": _num(portfolio.get("profit_percent"), 25.0, 0.0, 100000.0),
            "loss_percent": _num(portfolio.get("loss_percent"), 15.0, 0.0, 100.0),
            "move_percent": _num(portfolio.get("move_percent"), 5.0, 0.0, 100.0),
            "move_minutes": int(_num(portfolio.get("move_minutes"), 60, 5, 10080)),
        }

    notifications = raw.get("notifications")
    if isinstance(notifications, dict):
        defaults = DEFAULT_CONFIG["notifications"]
        cfg["notifications"] = {
            key: (bool(notifications.get(key, default)) if isinstance(default, bool) else _text(notifications.get(key, default)))
            for key, default in defaults.items()
        }

    cfg["autostart"] = bool(raw.get("autostart", False))
    geometry = _text(raw.get("window_geometry"))
    cfg["window_geometry"] = geometry if _GEOMETRY.match(geometry) else ""
    return cfg


class ConfigStore:
    """Thread-sichere Verwaltung der Einstellungen mit automatischem Speichern."""

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self._lock = threading.RLock()
        self._data = self._read()
        if not self.path.exists():
            self._save()

    @property
    def folder(self) -> Path:
        return self.path.parent

    def _read(self) -> dict:
        try:
            text = self.path.read_text(encoding="utf-8-sig")
        except FileNotFoundError:
            return normalize_config({})
        except OSError as exc:
            log.warning("Einstellungen konnten nicht gelesen werden: %s", exc)
            return normalize_config({})
        try:
            raw = json.loads(text)
        except ValueError:
            backup = self.path.with_name(self.path.stem + ".defekt" + self.path.suffix)
            try:
                shutil.copyfile(self.path, backup)
            except OSError:
                pass
            log.warning("config.json ist beschädigt – Standardwerte werden verwendet (Sicherung: %s)", backup)
            return normalize_config({})
        return normalize_config(raw)

    def _save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_name(self.path.name + ".tmp")
            tmp.write_text(json.dumps(self._data, indent=2, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, self.path)
        except OSError as exc:
            log.error("Einstellungen konnten nicht gespeichert werden: %s", exc)

    def get(self) -> dict:
        """Kopie der aktuellen Einstellungen."""
        with self._lock:
            return copy.deepcopy(self._data)

    def update(self, change) -> dict:
        """Ändert die Einstellungen über change(cfg), prüft sie und speichert sie."""
        with self._lock:
            data = copy.deepcopy(self._data)
            change(data)
            self._data = normalize_config(data)
            self._save()
            return copy.deepcopy(self._data)
