"""Konsolenmodus: Kurse und Alarme als Text (python -m krypto_waechter --konsole)."""

from __future__ import annotations

import sys
import time

from . import APP_NAME, __version__
from .config import coin_key, coin_label
from .formatting import format_change, format_money, format_price, format_time, window_label
from .monitor import Monitor


def _print_snapshot(snapshot, store) -> None:
    if not snapshot.fetched:
        return
    cfg = store.get()
    header = f"{'Coin':<28}{'Kurs':>18}" + "".join(f"{window_label(m):>13}" for m in snapshot.windows)
    print()
    print(f"{format_time(snapshot.time)}  {'(Demo)' if snapshot.demo else ''}")
    print(header)
    print("-" * len(header))
    for coin in cfg["coins"]:
        key = coin_key(coin)
        quote = snapshot.quotes.get(key)
        label = quote.label if quote else coin_label(coin)
        price = format_price(quote.price, quote.currency) if quote else "–"
        changes = snapshot.changes.get(key, {})
        cells = "".join(f"{format_change(changes.get(m)):>13}" for m in snapshot.windows)
        print(f"{label[:27]:<28}{price:>18}{cells}")
    for total in snapshot.totals:
        pl = f" · Gewinn/Verlust {format_money(total.pl, total.currency, signed=True)}" if total.pl is not None else ""
        print(f"Portfolio ({total.currency}): {format_money(total.value, total.currency)}{pl}")
    for alert in snapshot.alerts:
        print(f"*** ALARM: {alert.title} – {alert.message.replace(chr(10), ' · ')}")
    for error in snapshot.errors:
        print(f"Hinweis: {error}")
    sys.stdout.flush()


def run_console(store, notifier, demo: bool = False, once: bool = False, interval: int | None = None,
                alert_log=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")  # Windows-Konsole kennt nicht jedes Zeichen
        except (AttributeError, ValueError):
            pass
    print(f"{APP_NAME} {__version__} – Konsolenmodus{' (Demo, simulierte Kurse)' if demo else ''}")
    monitor = Monitor(store, notifier, lambda snap: _print_snapshot(snap, store), demo=demo, interval=interval,
                      alert_log=alert_log)
    if once:
        monitor.run_cycle()
        notifier.flush()
        return 0
    print("Beenden mit Strg+C")
    monitor.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        monitor.stop()
        print("Beendet.")
    return 0
