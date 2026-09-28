"""Überwachung im Hintergrund: Kurse abrufen, auswerten und Alarme auslösen."""

from __future__ import annotations

import csv
import logging
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from .config import coin_key
from .engine import KIND_LABELS, Engine
from .formatting import format_datetime
from .net import NetError, RateLimitError
from .providers import Binance, CoinGecko, DemoMarket

log = logging.getLogger(__name__)

DEMO_INTERVAL = 5  # Sekunden zwischen zwei Abrufen im Demo-Modus
MIN_GAP = 8  # Mindestabstand zwischen zwei Abrufen (schont die Anfrage-Limits)


@dataclass
class Snapshot:
    """Stand nach einer Aktualisierung – wird an Oberfläche bzw. Konsole übergeben."""

    time: float
    quotes: dict  # letzte bekannte Kurse der Watchlist
    fresh: set  # Coins, die gerade eben aktualisiert wurden
    changes: dict
    windows: list
    active: dict
    positions: list
    totals: list
    scanner: list
    alerts: list
    errors: list
    demo: bool = False
    fetched: bool = True  # False: nur neu berechnet (z. B. nach geänderten Einstellungen)


class Monitor:
    def __init__(self, store, notifier, on_update, demo: bool = False, interval: int | None = None,
                 coingecko_factory=CoinGecko, binance: Binance | None = None, alert_log: Path | None = None,
                 clock=time.time):
        self.store = store
        self.clock = clock
        self.notifier = notifier
        self.on_update = on_update
        self.demo = DemoMarket() if demo else None
        self.interval_override = interval or (DEMO_INTERVAL if demo else None)
        self.engine = Engine()
        self.binance = binance or Binance()
        self.alert_log = alert_log
        self.last_quotes: dict = {}
        self.scanner: list = []
        self.next_run = 0.0
        self._coingecko_factory = coingecko_factory
        self._coingecko = None
        self._coingecko_settings = None
        self._currency = None
        self._blocked_until: dict[str, float] = {}
        self._backoff: dict[str, float] = {}
        self._last_cycle = 0.0
        self._paused = False
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None

    # -- Steuerung -------------------------------------------------------------

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="Kursabruf", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def refresh_now(self) -> None:
        self._wake.set()

    @property
    def paused(self) -> bool:
        return self._paused

    def set_paused(self, paused: bool) -> None:
        self._paused = paused
        self._wake.set()

    def interval(self) -> int:
        return self.interval_override or self.store.get()["poll_interval"]

    def _run(self) -> None:
        while not self._stop.is_set():
            self._wake.clear()
            if not self._paused:
                gap = min(MIN_GAP, self.interval()) - (time.time() - self._last_cycle)
                try:
                    if gap > 0:
                        # Zu früh für einen neuen Abruf: sofort mit den bekannten Kursen neu rechnen
                        self.run_cycle(fetch=False)
                        if self._stop.wait(gap):
                            break
                    self.run_cycle()
                except Exception:
                    log.exception("Fehler bei der Aktualisierung")
            interval = self.interval()
            self.next_run = time.time() + interval
            self._wake.wait(interval)

    # -- Ein Durchlauf ---------------------------------------------------------

    def run_cycle(self, fetch: bool = True) -> Snapshot:
        """Kurse abrufen und auswerten (fetch=False: nur mit bekannten Kursen neu berechnen)."""
        cfg = self.store.get()
        now = self.clock()
        if fetch:
            self._last_cycle = now
        if cfg["currency"] != self._currency:
            if self._currency is not None:
                # Kurse in anderer Währung – alter Verlauf ist nicht mehr vergleichbar
                self.engine.reset("coingecko:")
                self.last_quotes = {k: q for k, q in self.last_quotes.items() if not k.startswith("coingecko:")}
                self.scanner = []
            self._currency = cfg["currency"]

        fresh, scanner, errors = self._fetch(cfg, now) if fetch else ({}, None, [])
        keys = {coin_key(c) for c in cfg["coins"]}
        fresh = {k: q for k, q in fresh.items() if k in keys}
        self.last_quotes.update(fresh)
        self.last_quotes = {k: q for k, q in self.last_quotes.items() if k in keys}
        if scanner is not None:
            self.scanner = scanner
        elif not cfg["scanner"]["enabled"]:
            self.scanner = []

        result = self.engine.evaluate(cfg, fresh, self.last_quotes, scanner or [], now)
        if result.alerts:
            self.notifier.send(result.alerts)
            self._log_alerts(result.alerts)
        snapshot = Snapshot(now, dict(self.last_quotes), set(fresh), result.changes, result.windows, result.active,
                            result.positions, result.totals, list(self.scanner), result.alerts, errors,
                            demo=self.demo is not None, fetched=fetch)
        self.on_update(snapshot)
        return snapshot

    def _fetch(self, cfg: dict, now: float):
        errors: list[str] = []
        fresh: dict = {}
        scanner = None
        coins, scan = cfg["coins"], cfg["scanner"]

        if self.demo:
            self.demo.tick(coins)
            fresh = self.demo.quotes(coins, cfg["currency"])
            if scan["enabled"]:
                scanner = self.demo.top(scan["top_n"], cfg["currency"])
            return fresh, scanner, errors

        gecko_ids = [c["id"] for c in coins if c["source"] == "coingecko"]
        if (gecko_ids or scan["enabled"]) and self._ready("coingecko", "CoinGecko", now, errors):
            gecko = self._gecko(cfg)
            try:
                missing = gecko_ids
                if scan["enabled"]:
                    # Die Top-Liste enthält meist schon alle Coins der Watchlist – spart Anfragen
                    scanner = gecko.top(scan["top_n"])
                    found = {q.coin_id: q for q in scanner}
                    fresh.update({found[cid].key: found[cid] for cid in gecko_ids if cid in found})
                    missing = [cid for cid in gecko_ids if cid not in found]
                if missing:
                    quotes = gecko.quotes(missing)
                    fresh.update(quotes)
                    errors.extend(f"CoinGecko kennt „{cid}“ nicht" for cid in missing if f"coingecko:{cid}" not in quotes)
                self._succeeded("coingecko")
            except NetError as exc:
                errors.append(self._failed("coingecko", exc, now, cfg))

        symbols = [c["id"] for c in coins if c["source"] == "binance"]
        if symbols and self._ready("binance", "Binance", now, errors):
            try:
                fresh.update(self.binance.quotes(symbols))
                self._succeeded("binance")
            except NetError as exc:
                errors.append(self._failed("binance", exc, now, cfg))
            errors.extend(f"Binance kennt das Handelspaar „{s}“ nicht" for s in symbols if s in self.binance.invalid_symbols)
        return fresh, scanner, errors

    def _gecko(self, cfg: dict) -> CoinGecko:
        settings = (cfg["currency"], cfg["coingecko_api_key"])
        if self._coingecko is None or settings != self._coingecko_settings:
            self._coingecko = self._coingecko_factory(*settings)
            self._coingecko_settings = settings
        return self._coingecko

    def _ready(self, source: str, label: str, now: float, errors: list) -> bool:
        until = self._blocked_until.get(source, 0.0)
        if now < until:
            errors.append(f"{label}: Pause wegen Anfrage-Limit – noch {int(until - now) + 1} s")
            return False
        return True

    def _succeeded(self, source: str) -> None:
        self._backoff.pop(source, None)
        self._blocked_until.pop(source, None)

    def _failed(self, source: str, exc: NetError, now: float, cfg: dict) -> str:
        message = str(exc)
        if isinstance(exc, RateLimitError):
            delay = min(max(self._backoff.get(source, 30.0) * 2, 60.0), 600.0)
            self._backoff[source] = delay
            self._blocked_until[source] = now + delay
            message += f" – nächster Versuch in {int(delay)} s"
            if source == "coingecko" and not cfg["coingecko_api_key"]:
                message += ". Tipp: Intervall erhöhen oder kostenlosen CoinGecko-API-Key eintragen"
        log.warning("Abruf fehlgeschlagen: %s", message)
        return message

    def search(self, source: str, query: str) -> list[dict]:
        """Coin-Suche für den Dialog „Coin hinzufügen“."""
        if self.demo:
            return self.demo.search(source, query)
        if source == "binance":
            return self.binance.search(query)
        return self._gecko(self.store.get()).search(query)

    def _log_alerts(self, alerts: list) -> None:
        """Alle Alarme zusätzlich in alarme.csv festhalten (lässt sich mit Excel öffnen)."""
        if not self.alert_log:
            return
        try:
            new = not self.alert_log.exists()
            with open(self.alert_log, "a", newline="", encoding="utf-8-sig" if new else "utf-8") as handle:
                writer = csv.writer(handle, delimiter=";")
                if new:
                    writer.writerow(["Zeit", "Art", "Richtung", "Meldung", "Details"])
                for alert in alerts:
                    writer.writerow([format_datetime(alert.time), KIND_LABELS.get(alert.kind, alert.kind),
                                     "steigt" if alert.direction == "up" else "fällt", alert.title,
                                     alert.message.replace("\n", " · ")])
        except OSError as exc:
            log.warning("alarme.csv konnte nicht geschrieben werden: %s", exc)
