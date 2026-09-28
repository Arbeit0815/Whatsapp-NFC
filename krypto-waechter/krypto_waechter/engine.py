"""Auswertung: Kursverläufe, Alarmregeln und Portfolio (Gewinn/Verlust)."""

from __future__ import annotations

import bisect
from dataclasses import dataclass

from .config import coin_key, coin_label
from .formatting import format_money, format_percent, format_price, window_label
from .providers import Quote, split_symbol

# Zeitfenster (Minuten), für die die Datenquellen fertige Prozentwerte liefern
API_WINDOWS = {60: "1h", 1440: "24h", 10080: "7d"}
PORTFOLIO_PREFIX = "__portfolio__"

KIND_LABELS = {
    "move": "Kursbewegung",
    "target": "Kursalarm",
    "profit": "Gewinn",
    "loss": "Verlust",
    "portfolio": "Portfolio",
    "scanner": "Markt-Scanner",
    "test": "Test",
}


@dataclass
class Alert:
    time: float
    kind: str  # move | target | profit | loss | portfolio | scanner | test
    direction: str  # up | down
    key: str
    title: str
    message: str
    icon: str = ""  # Emoji für Windows- und Telegram-Meldungen


@dataclass
class Position:
    """Ein Bestand im Portfolio."""

    key: str
    label: str
    currency: str
    amount: float
    buy_price: float
    price: float | None = None
    value: float | None = None
    cost: float | None = None
    pl: float | None = None
    pl_pct: float | None = None
    change_24h: float | None = None


@dataclass
class PortfolioTotal:
    """Summe aller Bestände in einer Währung."""

    currency: str
    value: float
    cost: float
    pl: float | None
    pl_pct: float | None
    change_24h: float | None
    change_24h_abs: float | None


@dataclass
class Evaluation:
    alerts: list
    changes: dict  # key -> {Minuten: Prozent}
    windows: list  # angezeigte Zeitfenster in Minuten
    active: dict  # key -> "up"/"down", wenn gerade eine Schwelle überschritten ist
    positions: list
    totals: list


def display_windows(cfg: dict) -> list[int]:
    """Aktive Alarm-Zeitfenster plus 1 Std, 24 Std und 7 Tage."""
    enabled = {rule["minutes"] for rule in cfg["move_rules"] if rule["enabled"]}
    return sorted(enabled | set(API_WINDOWS))


class PriceHistory:
    """Merkt sich die Kurse der letzten Stunden je Coin."""

    def __init__(self, max_age: float = 26 * 3600):
        self.max_age = max_age
        self._times: dict[str, list[float]] = {}
        self._prices: dict[str, list[float]] = {}

    def add(self, key: str, timestamp: float, price: float) -> None:
        times = self._times.setdefault(key, [])
        prices = self._prices.setdefault(key, [])
        if times and timestamp <= times[-1]:
            if timestamp == times[-1]:
                prices[-1] = price
            return
        times.append(timestamp)
        prices.append(price)
        cut = bisect.bisect_left(times, timestamp - self.max_age)
        if cut:
            del times[:cut]
            del prices[:cut]

    def change_percent(self, key: str, minutes: int, tolerance: float) -> float | None:
        """Veränderung gegenüber dem Kurs vor <minutes> Minuten.

        Liefert None, wenn es keinen Vergleichswert gibt, der höchstens <tolerance>
        Sekunden vom Zielzeitpunkt entfernt liegt (z. B. kurz nach dem Start oder
        wenn der PC im Standby war).
        """
        times = self._times.get(key)
        if not times or len(times) < 2:
            return None
        prices = self._prices[key]
        target = times[-1] - minutes * 60
        index = bisect.bisect_left(times, target)
        best = None
        for candidate in (index - 1, index):
            if 0 <= candidate < len(times) - 1:
                if best is None or abs(times[candidate] - target) < abs(times[best] - target):
                    best = candidate
        if best is None or abs(times[best] - target) > tolerance or prices[best] <= 0:
            return None
        return (prices[-1] / prices[best] - 1) * 100

    def samples(self, key: str) -> int:
        return len(self._times.get(key, ()))

    def retain(self, keys) -> None:
        for key in [k for k in self._times if k not in keys]:
            del self._times[key]
            del self._prices[key]

    def clear(self, prefix: str = "") -> None:
        self.retain({k for k in self._times if not k.startswith(prefix)})


@dataclass
class _Latch:
    level: float
    time: float


class AlertLatch:
    """Verhindert Alarm-Fluten.

    - Bedingung erfüllt und noch nicht gemeldet -> melden
    - Bewegung verschärft sich um eine weitere Stufe -> sofort erneut melden
    - Lage hat sich beruhigt und die Mindestpause ist vorbei -> wieder scharf
    """

    def __init__(self):
        self._state: dict[str, _Latch] = {}

    def check(self, alert_id: str, now: float, *, active: bool, level: float = 0.0,
              step: float | None = None, rearm: bool, cooldown: float) -> bool:
        state = self._state.get(alert_id)
        if active:
            if state is None or (step and level >= state.level + step):
                self._state[alert_id] = _Latch(level, now)
                return True
            return False
        if state is not None and rearm and now - state.time >= cooldown:
            del self._state[alert_id]
        return False

    def clear(self, prefix: str = "") -> None:
        for alert_id in [a for a in self._state if a.startswith(prefix)]:
            del self._state[alert_id]

    def prune(self, now: float, max_age: float) -> None:
        for alert_id in [a for a, s in self._state.items() if now - s.time > max_age]:
            del self._state[alert_id]


def _directions(pct: float):
    """Prüft Anstieg und Rückgang getrennt: +3 % -> (up, 3), (down, 0)."""
    return (("up", max(pct, 0.0)), ("down", max(-pct, 0.0)))


class Engine:
    """Wertet neue Kurse aus und erzeugt Alarme."""

    def __init__(self):
        self.history = PriceHistory()
        self.latch = AlertLatch()

    def reset(self, prefix: str = "") -> None:
        """Verlauf und Alarmzustände vergessen (z. B. nach einem Währungswechsel)."""
        self.history.clear(prefix)
        self.latch.clear(prefix)

    def change(self, quote: Quote, minutes: int, poll_interval: float) -> float | None:
        """Veränderung in Prozent: fertiger Wert der Datenquelle oder aus dem eigenen Verlauf."""
        label = API_WINDOWS.get(minutes)
        if label and quote.changes.get(label) is not None:
            return quote.changes[label]
        tolerance = max(minutes * 60 * 0.15, poll_interval * 1.5, 60.0)
        return self.history.change_percent(quote.key, minutes, tolerance)

    def evaluate(self, cfg: dict, fresh: dict, quotes: dict, scanner: list, now: float) -> Evaluation:
        """fresh: gerade abgerufene Kurse, quotes: letzte bekannte Kurse aller Coins."""
        poll = cfg["poll_interval"]
        quotes = {**quotes, **fresh}
        for key, quote in fresh.items():
            self.history.add(key, now, quote.price)
        self.history.retain(set(quotes))

        windows = display_windows(cfg)
        rules = [rule for rule in cfg["move_rules"] if rule["enabled"]]
        changes = {key: {m: self.change(q, m, poll) for m in windows} for key, q in quotes.items()}
        active = {}
        for key, values in changes.items():
            strongest = 0.0
            for rule in rules:
                pct = values.get(rule["minutes"])
                if pct is not None and abs(pct) >= rule["percent"] and abs(pct) / rule["percent"] > strongest:
                    strongest = abs(pct) / rule["percent"]
                    active[key] = "up" if pct > 0 else "down"

        cooldown = cfg["cooldown_minutes"] * 60
        coins = {coin_key(c): c for c in cfg["coins"]}
        alerts = []
        for key, quote in fresh.items():
            coin = coins.get(key)
            if coin is None:
                continue
            alert = self._move_alert(quote, rules, changes[key], cooldown, now)
            if alert:
                alerts.append(alert)
            alerts.extend(self._target_alerts(quote, coin, cooldown, now))

        positions, totals = self._portfolio(cfg, quotes, poll)
        alerts.extend(self._portfolio_alerts(cfg, positions, totals, quotes, fresh, cooldown, now))
        alerts.extend(self._scanner_alerts(cfg, scanner, cooldown, now))
        self.latch.prune(now, 3 * 86400)
        return Evaluation(alerts, changes, windows, active, positions, totals)

    # -- Kursbewegungen ------------------------------------------------------

    def _move_alert(self, quote: Quote, rules: list, changes: dict, cooldown: float, now: float) -> Alert | None:
        hits = []
        for rule in rules:
            minutes, threshold = rule["minutes"], rule["percent"]
            pct = changes.get(minutes)
            if pct is None:
                continue
            for direction, value in _directions(pct):
                if self.latch.check(f"{quote.key}|move|{minutes}|{direction}", now, active=value >= threshold,
                                    level=value, step=threshold, rearm=value < threshold * 0.5, cooldown=cooldown):
                    hits.append((minutes, pct, value / threshold))
        if not hits:
            return None
        minutes, pct, _ = max(hits, key=lambda hit: hit[2])
        rising = pct > 0
        title = f"{quote.label} {'steigt' if rising else 'fällt'}: {format_percent(pct)} in {window_label(minutes)}"
        lines = []
        if len(hits) > 1:
            lines.append(" · ".join(f"{format_percent(p)} in {window_label(m)}" for m, p, _ in sorted(hits)))
        price_line = f"Kurs: {format_price(quote.price, quote.currency)}"
        day = quote.changes.get("24h")
        if day is not None and all(m != 1440 for m, _, _ in hits):
            price_line += f" · 24 Std: {format_percent(day)}"
        lines.append(price_line)
        return Alert(now, "move", "up" if rising else "down", quote.key, title, "\n".join(lines),
                     icon="📈" if rising else "📉")

    def _target_alerts(self, quote: Quote, coin: dict, cooldown: float, now: float) -> list[Alert]:
        alerts = []
        price, currency = quote.price, quote.currency
        above, below = coin.get("alarm_above"), coin.get("alarm_below")
        # Der Zielpreis gehört zur Kennung: ein geänderter Kursalarm ist sofort wieder scharf
        if above and self.latch.check(f"{quote.key}|above|{above}", now, active=price >= above,
                                      rearm=price < above * 0.99, cooldown=cooldown):
            alerts.append(Alert(now, "target", "up", quote.key,
                                f"{quote.label} über {format_price(above, currency)}",
                                f"Kursziel erreicht – aktueller Kurs: {format_price(price, currency)}", icon="🎯"))
        if below and self.latch.check(f"{quote.key}|below|{below}", now, active=price <= below,
                                      rearm=price > below * 1.01, cooldown=cooldown):
            alerts.append(Alert(now, "target", "down", quote.key,
                                f"{quote.label} unter {format_price(below, currency)}",
                                f"Kursalarm – aktueller Kurs: {format_price(price, currency)}", icon="🎯"))
        return alerts

    # -- Portfolio -----------------------------------------------------------

    def _portfolio(self, cfg: dict, quotes: dict, poll: float):
        positions = []
        for coin in cfg["coins"]:
            if coin["amount"] <= 0:
                continue
            key = coin_key(coin)
            quote = quotes.get(key)
            buy = coin["buy_price"]
            if quote is None:
                currency = split_symbol(coin["id"])[1] if coin["source"] == "binance" else cfg["currency"].upper()
                positions.append(Position(key, coin_label(coin), currency, coin["amount"], buy))
                continue
            value = coin["amount"] * quote.price
            cost = coin["amount"] * buy if buy > 0 else None
            pl = value - cost if cost else None
            positions.append(Position(
                key, quote.label, quote.currency, coin["amount"], buy, quote.price, value, cost, pl,
                pl / cost * 100 if cost else None, self.change(quote, 1440, poll),
            ))
        totals = []
        for currency in dict.fromkeys(p.currency for p in positions if p.value is not None):
            group = [p for p in positions if p.currency == currency and p.value is not None]
            costed = [p for p in group if p.cost]
            cost = sum(p.cost for p in costed)
            pl = sum(p.pl for p in costed) if costed else None
            day = self._portfolio_change(group, quotes, 1440, poll)
            totals.append(PortfolioTotal(
                currency, sum(p.value for p in group), cost, pl, pl / cost * 100 if pl is not None and cost else None,
                day[0] if day else None, day[1] if day else None,
            ))
        return positions, totals

    def _portfolio_change(self, group: list, quotes: dict, minutes: int, poll: float):
        """Veränderung des Gesamtwerts (Prozent, Betrag) bei unveränderten Beständen."""
        now_total = then_total = 0.0
        known = False
        for position in group:
            pct = self.change(quotes[position.key], minutes, poll)
            now_total += position.value
            if pct is None or pct <= -100:
                then_total += position.value
            else:
                then_total += position.value / (1 + pct / 100)
                known = True
        if not known or then_total <= 0:
            return None
        return (now_total / then_total - 1) * 100, now_total - then_total

    def _portfolio_alerts(self, cfg, positions, totals, quotes, fresh, cooldown, now) -> list[Alert]:
        settings = cfg["portfolio"]
        profit, loss = settings["profit_percent"], settings["loss_percent"]
        alerts = []
        for p in positions:
            if p.pl_pct is None or p.key not in fresh:
                continue
            details = (f"Wert: {format_money(p.value, p.currency)} · Kaufpreis: {format_price(p.buy_price, p.currency)}"
                       f" · Kurs: {format_price(p.price, p.currency)}")
            if profit > 0 and self.latch.check(
                    f"{p.key}|profit", now, active=p.pl_pct >= profit, level=p.pl_pct, step=profit,
                    rearm=p.pl_pct < profit - max(2.0, profit * 0.2), cooldown=cooldown):
                alerts.append(Alert(now, "profit", "up", p.key,
                                    f"{p.label}: Gewinn {format_percent(p.pl_pct)} seit Kauf",
                                    f"Gewinn: {format_money(p.pl, p.currency, signed=True)}\n{details}", icon="💰"))
            if loss > 0 and self.latch.check(
                    f"{p.key}|loss", now, active=p.pl_pct <= -loss, level=-p.pl_pct, step=loss,
                    rearm=p.pl_pct > -loss + max(2.0, loss * 0.2), cooldown=cooldown):
                alerts.append(Alert(now, "loss", "down", p.key,
                                    f"{p.label}: Verlust {format_percent(p.pl_pct)} seit Kauf",
                                    f"Verlust: {format_money(p.pl, p.currency, signed=True)}\n{details}", icon="⚠️"))

        move, minutes = settings["move_percent"], settings["move_minutes"]
        if move <= 0:
            return alerts
        poll = cfg["poll_interval"]
        for total in totals:
            group = [p for p in positions if p.currency == total.currency and p.value is not None]
            if not any(p.key in fresh for p in group):
                continue
            result = self._portfolio_change(group, quotes, minutes, poll)
            if result is None:
                continue
            pct, delta = result
            key = f"{PORTFOLIO_PREFIX}:{total.currency}"
            for direction, value in _directions(pct):
                if self.latch.check(f"{key}|move|{direction}", now, active=value >= move, level=value, step=move,
                                    rearm=value < move * 0.5, cooldown=cooldown):
                    message = (f"Veränderung: {format_money(delta, total.currency, signed=True)}"
                               f" · Wert: {format_money(total.value, total.currency)}")
                    if total.pl is not None:
                        message += (f"\nGewinn/Verlust gesamt: {format_money(total.pl, total.currency, signed=True)}"
                                    f" ({format_percent(total.pl_pct)})")
                    alerts.append(Alert(now, "portfolio", direction, key,
                                        f"Portfolio {format_percent(pct)} in {window_label(minutes)}", message,
                                        icon="💼"))
        return alerts

    # -- Markt-Scanner -------------------------------------------------------

    def _scanner_alerts(self, cfg: dict, scanner: list, cooldown: float, now: float) -> list[Alert]:
        settings = cfg["scanner"]
        if not settings["enabled"] or not scanner:
            return []
        # Coins der Watchlist haben eigene, genauere Regeln
        watched = {c["id"] for c in cfg["coins"] if c["source"] == "coingecko"}
        alerts = []
        for quote in scanner:
            if quote.coin_id in watched:
                continue
            hits = []
            for label, minutes, threshold in (("1h", 60, settings["percent_1h"]), ("24h", 1440, settings["percent_24h"])):
                pct = quote.changes.get(label)
                if threshold <= 0 or pct is None:
                    continue
                for direction, value in _directions(pct):
                    if self.latch.check(f"{quote.key}|scan|{label}|{direction}", now, active=value >= threshold,
                                        level=value, step=threshold, rearm=value < threshold * 0.5,
                                        cooldown=cooldown):
                        hits.append((minutes, pct, value / threshold))
            if not hits:
                continue
            minutes, pct, _ = max(hits, key=lambda hit: hit[2])
            parts = [f"Rang {quote.rank}" if quote.rank else "", f"Kurs: {format_price(quote.price, quote.currency)}"]
            for label, text in (("1h", "1 Std"), ("24h", "24 Std")):
                if quote.changes.get(label) is not None:
                    parts.append(f"{text}: {format_percent(quote.changes[label])}")
            alerts.append(Alert(now, "scanner", "up" if pct > 0 else "down", quote.key,
                                f"Markt: {quote.label} {format_percent(pct)} in {window_label(minutes)}",
                                " · ".join(p for p in parts if p), icon="🔎"))
        return alerts
