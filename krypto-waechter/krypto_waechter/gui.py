"""Programmfenster (Tkinter – ist bei Python für Windows bereits dabei)."""

from __future__ import annotations

import logging
import queue
import re
import subprocess
import sys
import threading
import time
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import font as tkfont
from tkinter import messagebox, ttk

from . import APP_ID, APP_NAME, __version__, winutils
from .config import CURRENCIES, coin_key, coin_label, normalize_coin
from .engine import KIND_LABELS, Alert, display_windows
from .formatting import (format_amount, format_change, format_datetime, format_money, format_number,
                         format_percent, format_plain, format_price, format_time, parse_number,
                         price_decimals, window_label)
from .monitor import Monitor
from .notifier import find_telegram_chat_id, send_telegram
from .providers import SOURCE_LABELS, split_symbol

log = logging.getLogger(__name__)

ASSETS = Path(__file__).resolve().parent / "assets"
GREEN = "#15803d"
RED = "#b91c1c"
MUTED = "#6b7280"
ROW_TAGS = {
    "up": {"foreground": GREEN},
    "down": {"foreground": RED},
    "alarm_up": {"background": "#dcfce7", "foreground": "#14532d"},
    "alarm_down": {"background": "#fee2e2", "foreground": "#7f1d1d"},
    "stale": {"foreground": MUTED},
}
PORTFOLIO_WINDOWS = [(15, "15 Min"), (60, "1 Std"), (240, "4 Std"), (1440, "24 Std")]
COINGECKO_API_URL = "https://www.coingecko.com/en/api/pricing"


def run_in_background(root, func, on_done, owner=None) -> None:
    """func() in einem eigenen Thread ausführen und danach on_done(ergebnis, fehler) im Fenster-Thread aufrufen."""
    box = {}

    def worker():
        try:
            box["result"] = func()
        except Exception as exc:  # Fehler wird im Fenster angezeigt
            box["error"] = exc

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()

    def check():
        if thread.is_alive():
            root.after(100, check)
            return
        try:
            if owner is not None and not owner.winfo_exists():
                return
            on_done(box.get("result"), box.get("error"))
        except tk.TclError:
            pass  # Fenster wurde inzwischen geschlossen

    root.after(100, check)


def make_tree(parent, columns: list, height: int = 12, scale: float = 1.0):
    """Tabelle mit Scrollbalken. columns: [(id, Überschrift, Breite, Ausrichtung, dehnbar), …]"""
    frame = ttk.Frame(parent)
    tree = ttk.Treeview(frame, show="headings", height=height, selectmode="browse")
    scroll = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
    tree.configure(yscrollcommand=scroll.set)
    tree.grid(row=0, column=0, sticky="nsew")
    scroll.grid(row=0, column=1, sticky="ns")
    frame.columnconfigure(0, weight=1)
    frame.rowconfigure(0, weight=1)
    set_columns(tree, columns, scale)
    for tag, options in ROW_TAGS.items():
        tree.tag_configure(tag, **options)
    return frame, tree


def set_columns(tree, columns: list, scale: float = 1.0) -> None:
    """Spaltenbreiten in Pixeln bei 100 % Skalierung – werden an die Bildschirmskalierung angepasst."""
    tree.configure(columns=[c[0] for c in columns])
    for column_id, heading, width, anchor, stretch in columns:
        tree.heading(column_id, text=heading, anchor=anchor)
        tree.column(column_id, width=int(width * scale), minwidth=int(40 * scale), anchor=anchor, stretch=stretch)


def upsert(tree, iid: str, values: list, tags=()) -> None:
    if tree.exists(iid):
        tree.item(iid, values=values, tags=tags)
    else:
        tree.insert("", "end", iid=iid, values=values, tags=tags)


def keep_rows(tree, order: list) -> None:
    """Überzählige Zeilen entfernen und die Reihenfolge herstellen."""
    wanted = set(order)
    for iid in tree.get_children():
        if iid not in wanted:
            tree.delete(iid)
    for index, iid in enumerate(order):
        tree.move(iid, "", index)


def coin_currency(coin: dict, cfg: dict) -> str:
    if coin["source"] == "binance":
        return split_symbol(coin["id"])[1]
    return cfg["currency"].upper()


def change_tags(value) -> tuple:
    if not value:
        return ()
    return ("up",) if value > 0 else ("down",)


class App:
    """Hauptfenster mit Watchlist, Portfolio, Markt-Scanner und Alarmliste."""

    def __init__(self, root: tk.Tk, store, notifier, demo: bool = False, interval: int | None = None,
                 start_monitor: bool = True, alert_log: Path | None = None):
        self.root = root
        self.store = store
        self.notifier = notifier
        self.demo = demo
        self.alert_log = alert_log
        self.updates: queue.Queue = queue.Queue()
        self.snapshot = None
        self.unread = 0
        self.scanner_sort = ("h1", True)
        self.watch_windows = None
        self._closing = False

        cfg = store.get()
        self._setup_window(cfg)
        self._setup_style()
        self._build_menu()
        self._build_header()
        self._build_statusbar()
        self._build_notebook(cfg)

        self.monitor = Monitor(store, notifier, self.updates.put, demo=demo, interval=interval, alert_log=alert_log)
        self.render()
        if start_monitor:
            self.monitor.start()
            self.set_status("Kurse werden geladen …")
        root.protocol("WM_DELETE_WINDOW", self.close)
        self._poll_job = root.after(250, self._poll_updates)
        self._tick_job = root.after(1000, self._tick)

    # -- Aufbau ----------------------------------------------------------------

    def _setup_window(self, cfg: dict) -> None:
        self.root.title(APP_NAME + (" – Demo-Modus" if self.demo else ""))
        # Bildschirmskalierung von Windows (z. B. 150 %) – Tk rechnet sonst mit festen Pixeln
        self.scale = max(1.0, self.root.winfo_fpixels("1i") / 96.0)
        screen_w, screen_h = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        self.root.minsize(min(self.px(880), screen_w), min(self.px(500), screen_h))
        default = f"{min(self.px(1180), screen_w - 40)}x{min(self.px(680), screen_h - 90)}"
        try:
            self.root.geometry(cfg["window_geometry"].split("+")[0] or default)
        except tk.TclError:
            self.root.geometry(default)
        try:
            if sys.platform == "win32" and (ASSETS / "krypto-waechter.ico").exists():
                self.root.iconbitmap(default=str(ASSETS / "krypto-waechter.ico"))
            elif (ASSETS / "krypto-waechter.png").exists():
                self._icon = tk.PhotoImage(file=str(ASSETS / "krypto-waechter.png"))
                self.root.iconphoto(True, self._icon)
        except tk.TclError:
            pass

    def px(self, value: float) -> int:
        """Pixelwert passend zur Bildschirmskalierung."""
        return int(value * self.scale)

    def _setup_style(self) -> None:
        style = ttk.Style(self.root)
        if sys.platform != "win32" and "clam" in style.theme_names():
            style.theme_use("clam")
        base = tkfont.nametofont("TkDefaultFont")
        base.configure(size=10)
        for name in ("TkTextFont", "TkHeadingFont", "TkMenuFont"):
            try:
                tkfont.nametofont(name).configure(size=10)
            except tk.TclError:
                pass
        self.font_bold = base.copy()
        self.font_bold.configure(weight="bold")
        self.font_title = base.copy()
        self.font_title.configure(size=16, weight="bold")
        # Zeilenhöhe passend zur Schrift (sonst zu eng bei Bildschirmskalierung)
        style.configure("Treeview", rowheight=int(base.metrics("linespace") * 1.6))
        style.configure("Treeview.Heading", font=self.font_bold)
        style.configure("Title.TLabel", font=self.font_title)
        style.configure("Bold.TLabel", font=self.font_bold)
        style.configure("Muted.TLabel", foreground=MUTED)
        style.configure("Status.TLabel", padding=(12, 5))
        style.configure("Error.TLabel", padding=(12, 5), foreground=RED)
        style.configure("Summary.TLabel", font=self.font_bold, padding=(2, 8, 2, 2))

        # Farbige Zeilen auch mit älteren Tk-Versionen
        def fixed(option):
            return [e for e in style.map("Treeview", query_opt=option) if e[:2] != ("!disabled", "!selected")]

        style.map("Treeview", foreground=fixed("foreground"), background=fixed("background"))

    def _build_menu(self) -> None:
        menubar = tk.Menu(self.root)
        file_menu = tk.Menu(menubar, tearoff=False)
        file_menu.add_command(label="Einstellungen …", command=self.open_settings)
        file_menu.add_command(label="Ordner mit Einstellungen und Protokollen öffnen", command=self.open_folder)
        file_menu.add_separator()
        file_menu.add_command(label="Beenden", command=self.close)
        menubar.add_cascade(label="Datei", menu=file_menu)
        help_menu = tk.Menu(menubar, tearoff=False)
        help_menu.add_command(label="Test-Benachrichtigung senden", command=self.test_notification)
        if not self.demo:
            help_menu.add_command(label="Demo-Modus starten (simulierte Kurse)", command=self.start_demo)
        help_menu.add_separator()
        help_menu.add_command(label=f"Über {APP_NAME}", command=self.show_about)
        menubar.add_cascade(label="Hilfe", menu=help_menu)
        self.root.configure(menu=menubar)

    def _build_header(self) -> None:
        if self.demo:
            tk.Label(self.root, text="DEMO-MODUS – die Kurse sind simuliert, damit du die Alarme ausprobieren kannst",
                     bg="#f59e0b", fg="#1f2937", font=self.font_bold, pady=4).pack(fill="x")
        header = ttk.Frame(self.root, padding=(14, 10, 14, 6))
        header.pack(fill="x")
        titles = ttk.Frame(header)
        titles.pack(side="left")
        ttk.Label(titles, text=APP_NAME, style="Title.TLabel").pack(anchor="w")
        ttk.Label(titles, text="Überwacht deine Coins und meldet starke Gewinne und Verluste.",
                  style="Muted.TLabel").pack(anchor="w")
        buttons = ttk.Frame(header)
        buttons.pack(side="right")
        ttk.Button(buttons, text="Jetzt aktualisieren", command=self.refresh_now).pack(side="left", padx=3)
        self.pause_button = ttk.Button(buttons, text="Pausieren", command=self.toggle_pause)
        self.pause_button.pack(side="left", padx=3)
        ttk.Button(buttons, text="Test-Alarm", command=self.test_notification).pack(side="left", padx=3)
        ttk.Button(buttons, text="Einstellungen", command=self.open_settings).pack(side="left", padx=3)

    def _build_statusbar(self) -> None:
        bar = ttk.Frame(self.root)
        bar.pack(side="bottom", fill="x")
        ttk.Separator(self.root, orient="horizontal").pack(side="bottom", fill="x")
        self.status = ttk.Label(bar, text="", style="Status.TLabel", anchor="w")
        self.status.pack(side="left", fill="x", expand=True)
        self.countdown = ttk.Label(bar, text="", style="Status.TLabel")
        self.countdown.pack(side="right")

    def _build_notebook(self, cfg: dict) -> None:
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill="both", expand=True, padx=12, pady=(2, 8))
        self.tab_watch = self._build_watch_tab(cfg)
        self.tab_portfolio = self._build_portfolio_tab()
        self.tab_scanner = self._build_scanner_tab()
        self.tab_alerts = self._build_alerts_tab()
        self.notebook.bind("<<NotebookTabChanged>>", self._on_tab_changed)

    def _watch_columns(self, windows: list) -> list:
        columns = [("coin", "Coin", 230, "w", True), ("price", "Kurs", 135, "e", False)]
        columns += [(f"w{m}", window_label(m), 100, "e", False) for m in windows]
        columns += [("target", "Kursalarm", 200, "w", False), ("source", "Quelle", 90, "w", False)]
        return columns

    def _build_watch_tab(self, cfg: dict):
        frame = ttk.Frame(self.notebook, padding=8)
        self.notebook.add(frame, text="Watchlist")
        tree_frame, self.watch_tree = make_tree(frame, self._watch_columns(display_windows(cfg)), scale=self.scale)
        tree_frame.pack(fill="both", expand=True)
        self.watch_tree.bind("<Double-1>", lambda event: self.edit_coin())
        self.watch_tree.bind("<Delete>", lambda event: self.remove_coin())
        bar = ttk.Frame(frame, padding=(0, 8, 0, 0))
        bar.pack(fill="x")
        ttk.Button(bar, text="Coin hinzufügen …", command=self.add_coin).pack(side="left")
        ttk.Button(bar, text="Bearbeiten …", command=self.edit_coin).pack(side="left", padx=4)
        ttk.Button(bar, text="Entfernen", command=self.remove_coin).pack(side="left")
        ttk.Button(bar, text="▲", width=3, command=lambda: self.move_coin(-1)).pack(side="left", padx=(14, 2))
        ttk.Button(bar, text="▼", width=3, command=lambda: self.move_coin(1)).pack(side="left")
        ttk.Label(bar, text="Doppelklick auf einen Coin: Bestand, Kaufpreis und Kursalarm eintragen",
                  style="Muted.TLabel").pack(side="right")
        return frame

    def _build_portfolio_tab(self):
        frame = ttk.Frame(self.notebook, padding=8)
        self.notebook.add(frame, text="Portfolio")
        columns = [("coin", "Coin", 220, "w", True), ("amount", "Menge", 110, "e", False),
                   ("buy", "Kaufpreis", 125, "e", False), ("price", "Kurs", 125, "e", False),
                   ("value", "Wert", 130, "e", False), ("pl", "Gewinn/Verlust", 145, "e", False),
                   ("plpct", "G/V %", 95, "e", False), ("d24", "24 Std", 100, "e", False)]
        tree_frame, self.portfolio_tree = make_tree(frame, columns, scale=self.scale)
        tree_frame.pack(fill="both", expand=True)
        self.portfolio_tree.tag_configure("total", font=self.font_bold)
        self.portfolio_tree.bind("<Double-1>", lambda event: self.edit_coin(self._selected(self.portfolio_tree)))
        self.portfolio_summary = ttk.Label(frame, text="", style="Summary.TLabel", justify="left")
        self.portfolio_summary.pack(fill="x")
        bar = ttk.Frame(frame, padding=(0, 6, 0, 0))
        bar.pack(fill="x")
        ttk.Button(bar, text="Bestand bearbeiten …",
                   command=lambda: self.edit_coin(self._selected(self.portfolio_tree))).pack(side="left")
        ttk.Label(bar, text="Neue Bestände: in der Watchlist doppelt auf den Coin klicken und Menge + Kaufpreis eintragen",
                  style="Muted.TLabel").pack(side="right")
        return frame

    def _build_scanner_tab(self):
        frame = ttk.Frame(self.notebook, padding=8)
        self.notebook.add(frame, text="Markt-Scanner")
        self.scanner_info = ttk.Label(frame, text="", style="Muted.TLabel")
        self.scanner_info.pack(fill="x", pady=(0, 6))
        columns = [("rank", "#", 55, "e", False), ("coin", "Coin", 250, "w", True), ("price", "Kurs", 135, "e", False),
                   ("h1", "1 Std", 105, "e", False), ("h24", "24 Std", 105, "e", False), ("d7", "7 Tage", 105, "e", False)]
        tree_frame, self.scanner_tree = make_tree(frame, columns, scale=self.scale)
        tree_frame.pack(fill="both", expand=True)
        for column_id, *_ in columns:
            self.scanner_tree.heading(column_id, command=lambda c=column_id: self.sort_scanner(c))
        bar = ttk.Frame(frame, padding=(0, 8, 0, 0))
        bar.pack(fill="x")
        ttk.Button(bar, text="Zur Watchlist hinzufügen", command=self.add_from_scanner).pack(side="left")
        ttk.Label(bar, text="★ = bereits in der Watchlist · Spaltenkopf anklicken zum Sortieren",
                  style="Muted.TLabel").pack(side="right")
        return frame

    def _build_alerts_tab(self):
        frame = ttk.Frame(self.notebook, padding=8)
        self.notebook.add(frame, text="Alarme")
        columns = [("time", "Zeit", 175, "w", False), ("kind", "Art", 120, "w", False),
                   ("title", "Meldung", 380, "w", True), ("detail", "Details", 460, "w", True)]
        tree_frame, self.alert_tree = make_tree(frame, columns, scale=self.scale)
        tree_frame.pack(fill="both", expand=True)
        bar = ttk.Frame(frame, padding=(0, 8, 0, 0))
        bar.pack(fill="x")
        ttk.Button(bar, text="Liste leeren", command=self.clear_alerts).pack(side="left")
        if self.alert_log:
            ttk.Button(bar, text="alarme.csv öffnen", command=self.open_alert_log).pack(side="left", padx=4)
            ttk.Label(bar, text="Alle Alarme werden zusätzlich in alarme.csv gespeichert (lässt sich mit Excel öffnen)",
                      style="Muted.TLabel").pack(side="right")
        return frame

    # -- Anzeige -----------------------------------------------------------------

    def render(self) -> None:
        cfg = self.store.get()
        self.render_watchlist(cfg)
        self.render_portfolio(cfg)
        self.render_scanner(cfg)

    def render_watchlist(self, cfg: dict) -> None:
        snap = self.snapshot
        windows = snap.windows if snap else display_windows(cfg)
        if windows != self.watch_windows:
            set_columns(self.watch_tree, self._watch_columns(windows), self.scale)
            self.watch_windows = windows
        order = []
        for coin in cfg["coins"]:
            key = coin_key(coin)
            quote = snap.quotes.get(key) if snap else None
            changes = snap.changes.get(key, {}) if snap else {}
            currency = quote.currency if quote else coin_currency(coin, cfg)
            missing = "keine Daten" if snap and snap.fetched else "lädt …"
            values = [quote.label if quote else coin_label(coin),
                      format_price(quote.price, quote.currency) if quote else missing]
            values += [format_change(changes.get(m)) for m in windows]
            values += [self._target_text(coin, currency), SOURCE_LABELS.get(coin["source"], coin["source"])]
            if snap and snap.active.get(key):
                tags = ("alarm_" + snap.active[key],)
            elif quote and key not in snap.fresh and snap.fetched:
                tags = ("stale",)
            else:
                tags = change_tags(changes.get(1440))
            upsert(self.watch_tree, key, values, tags)
            order.append(key)
        keep_rows(self.watch_tree, order)

    @staticmethod
    def _target_text(coin: dict, currency: str) -> str:
        parts = []
        if coin.get("alarm_above"):
            parts.append(f"über {format_price(coin['alarm_above'], currency)}")
        if coin.get("alarm_below"):
            parts.append(f"unter {format_price(coin['alarm_below'], currency)}")
        return " · ".join(parts)

    def render_portfolio(self, cfg: dict) -> None:
        snap = self.snapshot
        positions = snap.positions if snap else []
        totals = snap.totals if snap else []
        order = []
        for p in positions:
            upsert(self.portfolio_tree, p.key, [
                p.label, format_amount(p.amount),
                format_price(p.buy_price, p.currency) if p.buy_price else "–",
                format_price(p.price, p.currency) if p.price else "lädt …",
                format_money(p.value, p.currency), format_money(p.pl, p.currency, signed=True),
                format_percent(p.pl_pct), format_change(p.change_24h),
            ], change_tags(p.pl))
            order.append(p.key)
        for total in totals:
            iid = f"total:{total.currency}"
            upsert(self.portfolio_tree, iid, [
                "Summe" + (f" {total.currency}" if len(totals) > 1 else ""), "", "", "",
                format_money(total.value, total.currency), format_money(total.pl, total.currency, signed=True),
                format_percent(total.pl_pct), format_change(total.change_24h),
            ], ("total",) + change_tags(total.pl))
            order.append(iid)
        keep_rows(self.portfolio_tree, order)

        if totals:
            lines = []
            for t in totals:
                line = f"Gesamtwert {format_money(t.value, t.currency)}"
                if t.pl is not None:
                    line += (f"   ·   Einstand {format_money(t.cost, t.currency)}   ·   Gewinn/Verlust "
                             f"{format_money(t.pl, t.currency, signed=True)} ({format_percent(t.pl_pct)})")
                if t.change_24h is not None:
                    line += (f"   ·   24 Std: {format_money(t.change_24h_abs, t.currency, signed=True)} "
                             f"({format_percent(t.change_24h)})")
                lines.append(line)
            text = "\n".join(lines)
        elif any(c["amount"] > 0 for c in cfg["coins"]):
            text = "Kurse werden geladen …"
        else:
            text = ("Noch keine Bestände eingetragen. In der Watchlist doppelt auf einen Coin klicken und "
                    "Menge sowie Kaufpreis eintragen – dann siehst du hier Gewinn und Verlust.")
        self.portfolio_summary.configure(text=text)

    def render_scanner(self, cfg: dict) -> None:
        settings = cfg["scanner"]
        snap = self.snapshot
        if not settings["enabled"]:
            self.scanner_info.configure(text="Der Markt-Scanner ist ausgeschaltet (Einstellungen → Alarme).")
        else:
            limits = [f"±{format_plain(settings['percent_1h'])} % in 1 Std" if settings["percent_1h"] else "",
                      f"±{format_plain(settings['percent_24h'])} % in 24 Std" if settings["percent_24h"] else ""]
            limits = " oder ".join(x for x in limits if x) or "keine Schwelle eingestellt"
            self.scanner_info.configure(text=f"Die {settings['top_n']} größten Coins nach Marktkapitalisierung "
                                             f"(CoinGecko). Alarm bei {limits} – Coins der Watchlist ausgenommen.")
        quotes = list(snap.scanner) if snap else []
        column, descending = self.scanner_sort
        getters = {
            "rank": lambda q: q.rank, "coin": lambda q: q.label.lower(), "price": lambda q: q.price,
            "h1": lambda q: q.changes.get("1h"), "h24": lambda q: q.changes.get("24h"),
            "d7": lambda q: q.changes.get("7d"),
        }
        getter = getters[column]
        present = sorted((q for q in quotes if getter(q) is not None), key=getter, reverse=descending)
        quotes = present + [q for q in quotes if getter(q) is None]
        watched = {coin_key(c) for c in cfg["coins"]}
        order = []
        for q in quotes:
            h1, h24 = q.changes.get("1h"), q.changes.get("24h")
            tags = change_tags(h24)
            for pct, limit in ((h1, settings["percent_1h"]), (h24, settings["percent_24h"])):
                if pct is not None and limit and abs(pct) >= limit:
                    tags = ("alarm_up",) if pct > 0 else ("alarm_down",)
            upsert(self.scanner_tree, q.key, [
                q.rank or "", ("★ " if q.key in watched else "") + q.label, format_price(q.price, q.currency),
                format_change(h1), format_change(h24), format_change(q.changes.get("7d")),
            ], tags)
            order.append(q.key)
        keep_rows(self.scanner_tree, order)
        headings = {"rank": "#", "coin": "Coin", "price": "Kurs", "h1": "1 Std", "h24": "24 Std", "d7": "7 Tage"}
        for column_id, text in headings.items():
            marker = (" ▼" if descending else " ▲") if column_id == column else ""
            self.scanner_tree.heading(column_id, text=text + marker)

    def sort_scanner(self, column: str) -> None:
        current, descending = self.scanner_sort
        if column == current:
            descending = not descending
        else:
            descending = column not in ("rank", "coin")
        self.scanner_sort = (column, descending)
        self.render_scanner(self.store.get())

    def add_alerts(self, alerts: list) -> None:
        for alert in alerts:
            self.alert_tree.insert("", 0, values=(format_datetime(alert.time), KIND_LABELS.get(alert.kind, alert.kind),
                                                  alert.title, alert.message.replace("\n", " · ")),
                                   tags=(alert.direction,))
        for iid in self.alert_tree.get_children()[500:]:
            self.alert_tree.delete(iid)
        if alerts and self.notebook.select() != str(self.tab_alerts):
            self.unread += len(alerts)
            self.notebook.tab(self.tab_alerts, text=f"Alarme ({self.unread})")

    def _on_tab_changed(self, event=None) -> None:
        if self.notebook.select() == str(self.tab_alerts) and self.unread:
            self.unread = 0
            self.notebook.tab(self.tab_alerts, text="Alarme")

    def set_status(self, text: str, error: bool = False) -> None:
        self.status.configure(text=text, style="Error.TLabel" if error else "Status.TLabel")

    def apply_snapshot(self, snapshot, alerts: list) -> None:
        self.snapshot = snapshot
        self.render()
        if alerts:
            self.add_alerts(alerts)
            self._attention()
        if not snapshot.fetched:
            return
        if snapshot.errors:
            self.set_status(" | ".join(dict.fromkeys(snapshot.errors)), error=True)
        else:
            text = f"Aktualisiert um {format_time(snapshot.time)} · {len(snapshot.quotes)} Coins in der Watchlist"
            if snapshot.scanner:
                text += f" · Markt-Scanner: {len(snapshot.scanner)} Coins"
            if alerts:
                text += f" · {len(alerts)} neue{'r' if len(alerts) == 1 else ''} Alarm{'e' if len(alerts) > 1 else ''}"
            self.set_status(text)

    def _attention(self) -> None:
        """Taskleistensymbol blinken lassen, wenn das Fenster nicht im Vordergrund ist."""
        if not self.store.get()["notifications"]["flash"] or not winutils.IS_WINDOWS:
            return
        try:
            if self.root.focus_displayof() is None:
                winutils.flash_window(int(self.root.wm_frame(), 16))
        except (tk.TclError, ValueError, OSError):
            pass

    def _poll_updates(self) -> None:
        latest, alerts = None, []
        try:
            while True:
                snapshot = self.updates.get_nowait()
                latest = snapshot
                alerts.extend(snapshot.alerts)
        except queue.Empty:
            pass
        if latest is not None:
            self.apply_snapshot(latest, alerts)
        if not self._closing:
            self._poll_job = self.root.after(250, self._poll_updates)

    def _tick(self) -> None:
        if self._closing:
            return
        if self.monitor.paused:
            text = "Pausiert"
        elif self.monitor.next_run:
            remaining = int(self.monitor.next_run - time.time())
            text = f"Nächste Aktualisierung in {remaining} s" if remaining > 0 else "Aktualisiere …"
        else:
            text = ""
        self.countdown.configure(text=text)
        self._tick_job = self.root.after(1000, self._tick)

    # -- Aktionen ----------------------------------------------------------------

    @staticmethod
    def _selected(tree):
        selection = tree.selection()
        return selection[0] if selection else None

    def _find_coin(self, key):
        for coin in self.store.get()["coins"]:
            if coin_key(coin) == key:
                return coin
        return None

    def _config_changed(self, select: str | None = None) -> None:
        self.render()
        if select and self.watch_tree.exists(select):
            self.watch_tree.selection_set(select)
            self.watch_tree.see(select)
        self.monitor.refresh_now()

    def add_coin(self) -> None:
        dialog = CoinDialog(self)
        self.root.wait_window(dialog)
        coin = normalize_coin(dialog.result) if dialog.result else None
        if not coin:
            return
        key = coin_key(coin)
        if self._find_coin(key):
            messagebox.showinfo(APP_NAME, f"{coin_label(coin)} ist bereits in der Watchlist.", parent=self.root)
            return
        self.store.update(lambda cfg: cfg["coins"].append(coin))
        self.notebook.select(self.tab_watch)
        self._config_changed(select=key)
        self.set_status(f"{coin_label(coin)} hinzugefügt – Kurs wird geladen …")

    def edit_coin(self, key: str | None = None) -> None:
        key = key or self._selected(self.watch_tree)
        coin = self._find_coin(key) if key else None
        if coin is None:
            messagebox.showinfo(APP_NAME, "Bitte zuerst einen Coin in der Liste auswählen.", parent=self.root)
            return
        dialog = CoinDialog(self, coin)
        self.root.wait_window(dialog)
        if not dialog.result:
            return

        def change(cfg):
            cfg["coins"] = [dialog.result if coin_key(c) == key else c for c in cfg["coins"]]

        self.store.update(change)
        self._config_changed(select=key)

    def remove_coin(self) -> None:
        key = self._selected(self.watch_tree)
        coin = self._find_coin(key) if key else None
        if coin is None:
            messagebox.showinfo(APP_NAME, "Bitte zuerst einen Coin in der Liste auswählen.", parent=self.root)
            return
        question = f"{coin_label(coin)} aus der Watchlist entfernen?"
        if coin["amount"] > 0:
            question += "\n\nDer eingetragene Bestand wird dabei ebenfalls gelöscht."
        if messagebox.askyesno("Coin entfernen", question, parent=self.root):
            self.store.update(lambda cfg: cfg.update(coins=[c for c in cfg["coins"] if coin_key(c) != key]))
            self._config_changed()

    def move_coin(self, delta: int) -> None:
        key = self._selected(self.watch_tree)
        if not key:
            return

        def change(cfg):
            keys = [coin_key(c) for c in cfg["coins"]]
            if key not in keys:
                return
            index = keys.index(key)
            target = index + delta
            if 0 <= target < len(keys):
                coins = cfg["coins"]
                coins[index], coins[target] = coins[target], coins[index]

        self.store.update(change)
        self.render()
        self.watch_tree.selection_set(key)
        self.watch_tree.see(key)

    def add_from_scanner(self) -> None:
        key = self._selected(self.scanner_tree)
        quote = next((q for q in self.snapshot.scanner if q.key == key), None) if key and self.snapshot else None
        if quote is None:
            messagebox.showinfo(APP_NAME, "Bitte zuerst einen Coin in der Liste auswählen.", parent=self.root)
            return
        if self._find_coin(key):
            messagebox.showinfo(APP_NAME, f"{quote.label} ist bereits in der Watchlist.", parent=self.root)
            return
        coin = {"source": "coingecko", "id": quote.coin_id, "name": quote.name, "symbol": quote.symbol}
        self.store.update(lambda cfg: cfg["coins"].append(coin))
        self._config_changed(select=key)
        self.set_status(f"{quote.label} zur Watchlist hinzugefügt.")

    def open_settings(self) -> None:
        before = self.store.get()
        dialog = SettingsDialog(self)
        self.root.wait_window(dialog)
        if dialog.result is None:
            return
        after = self.store.update(dialog.result)
        if winutils.IS_WINDOWS and after["autostart"] != before["autostart"]:
            try:
                winutils.set_autostart(after["autostart"])
            except OSError as exc:
                messagebox.showerror(APP_NAME, f"Autostart konnte nicht geändert werden: {exc}", parent=self.root)
        self._config_changed()
        self.set_status("Einstellungen gespeichert.")

    def refresh_now(self) -> None:
        self.set_status("Aktualisiere …")
        self.monitor.refresh_now()

    def toggle_pause(self) -> None:
        paused = not self.monitor.paused
        self.monitor.set_paused(paused)
        self.pause_button.configure(text="Fortsetzen" if paused else "Pausieren")
        self.set_status("Überwachung pausiert – es werden keine Kurse abgerufen." if paused else "Überwachung läuft wieder.")

    def test_notification(self) -> None:
        alert = Alert(time.time(), "test", "up", "test", "Test-Benachrichtigung",
                      "So sieht ein Alarm vom Krypto-Wächter aus.", icon="🔔")
        self.add_alerts([alert])
        self.set_status("Test-Benachrichtigung wird gesendet …")

        def done(results, error):
            if error:
                self.set_status(f"Test fehlgeschlagen: {error}", error=True)
            elif not results:
                self.set_status("Keine Benachrichtigung eingeschaltet (Einstellungen → Benachrichtigungen).", error=True)
            else:
                parts = [f"{channel} ✓" if not problem else f"{channel}: {problem}" for channel, problem in results.items()]
                failed = any(results.values())
                self.set_status("Test gesendet – " + " · ".join(parts), error=failed)
                if failed:
                    messagebox.showwarning(APP_NAME, "Nicht alles hat geklappt:\n\n" + "\n".join(parts), parent=self.root)

        run_in_background(self.root, lambda: self.notifier.deliver([alert]), done)

    def clear_alerts(self) -> None:
        self.alert_tree.delete(*self.alert_tree.get_children())
        self.unread = 0
        self.notebook.tab(self.tab_alerts, text="Alarme")

    def open_folder(self) -> None:
        try:
            winutils.open_path(self.store.folder)
        except OSError as exc:
            messagebox.showerror(APP_NAME, f"Ordner konnte nicht geöffnet werden: {exc}", parent=self.root)

    def open_alert_log(self) -> None:
        if not self.alert_log or not self.alert_log.exists():
            messagebox.showinfo(APP_NAME, "Bisher wurden noch keine Alarme gespeichert.", parent=self.root)
            return
        try:
            winutils.open_path(self.alert_log)
        except OSError as exc:
            messagebox.showerror(APP_NAME, f"Datei konnte nicht geöffnet werden: {exc}", parent=self.root)

    def start_demo(self) -> None:
        """Startet ein zweites Fenster mit simulierten Kursen – zum gefahrlosen Ausprobieren der Alarme."""
        try:
            subprocess.Popen(winutils.program_command() + ["--demo"])
        except OSError as exc:
            messagebox.showerror(APP_NAME, f"Demo-Modus konnte nicht gestartet werden: {exc}", parent=self.root)
            return
        self.set_status("Demo-Modus wird in einem eigenen Fenster gestartet …")

    def show_about(self) -> None:
        messagebox.showinfo(
            f"Über {APP_NAME}",
            f"{APP_NAME} {__version__}\n\n"
            "Überwacht Kryptowährungen und meldet starke Kursbewegungen, erreichte Kursziele sowie Gewinne "
            "und Verluste deines Portfolios.\n\n"
            "Kursdaten: CoinGecko (Durchschnitt vieler Börsen) und Binance.\n"
            f"Einstellungen und Protokolle: {self.store.folder}\n\n"
            "Keine Anlageberatung – alle Angaben ohne Gewähr.",
            parent=self.root,
        )

    def close(self) -> None:
        if self._closing:
            return
        self._closing = True
        for job in (self._poll_job, self._tick_job):
            try:
                self.root.after_cancel(job)
            except tk.TclError:
                pass
        try:
            if self.root.state() == "normal":
                size = f"{self.root.winfo_width()}x{self.root.winfo_height()}"
                self.store.update(lambda cfg: cfg.update(window_geometry=size))
        except tk.TclError:
            pass
        self.monitor.stop()
        self.root.destroy()


# ---------------------------------------------------------------------------
# Dialoge
# ---------------------------------------------------------------------------

class Dialog(tk.Toplevel):
    """Grundgerüst für modale Dialoge."""

    def __init__(self, app: App, title: str):
        super().__init__(app.root)
        self.app = app
        self.result = None
        self.withdraw()
        self.title(title)
        self.transient(app.root)
        self.resizable(False, False)
        self.body = ttk.Frame(self, padding=16)
        self.body.pack(fill="both", expand=True)
        self.bind("<Escape>", lambda event: self.destroy())

    def show(self, focus=None) -> None:
        self.update_idletasks()
        parent = self.app.root
        x = parent.winfo_rootx() + max(0, (parent.winfo_width() - self.winfo_reqwidth()) // 2)
        y = parent.winfo_rooty() + max(0, (parent.winfo_height() - self.winfo_reqheight()) // 3)
        self.geometry(f"+{x}+{y}")
        self.deiconify()
        self._grab()
        if focus is not None:
            focus.focus_set()

    def _grab(self) -> None:
        try:
            self.grab_set()
        except tk.TclError:  # Fenster noch nicht sichtbar
            self.after(100, self._grab)

    def number_field(self, parent, row: int, label: str, value, amount: bool = False):
        """Eingabefeld für Zahlen mit Vorschau, wie die Eingabe verstanden wird."""
        if value:
            # verlustfrei vorbelegen, damit Speichern ohne Änderung nichts rundet
            text = format_amount(value) if amount else format_plain(value, max_decimals=10)
        else:
            text = ""
        var = tk.StringVar(value=text)
        label_widget = ttk.Label(parent, text=label, width=38)
        label_widget.grid(row=row, column=0, sticky="w", pady=3)
        entry = ttk.Entry(parent, textvariable=var, width=18, justify="right")
        entry.grid(row=row, column=1, sticky="w", padx=8, pady=3)
        preview = ttk.Label(parent, text="", width=24, style="Muted.TLabel")
        preview.grid(row=row, column=2, sticky="w")

        def update(*_):
            try:
                number = parse_number(var.get())
            except ValueError:
                preview.configure(text="keine gültige Zahl", foreground=RED)
                return
            if number is None:
                preview.configure(text="", foreground=MUTED)
            else:
                shown = format_amount(number) if amount else format_number(number, price_decimals(number))
                preview.configure(text=f"= {shown}", foreground=MUTED)

        var.trace_add("write", update)
        update()
        return var, entry, label_widget

    @staticmethod
    def read_number(var, label: str, minimum: float | None = 0.0):
        try:
            value = parse_number(var.get())
        except ValueError:
            raise ValueError(f"„{var.get()}“ ist bei „{label}“ keine gültige Zahl.") from None
        if value is not None and minimum is not None and value < minimum:
            raise ValueError(f"„{label}“ muss mindestens {format_plain(minimum)} sein.")
        return value


class CoinDialog(Dialog):
    """Coin suchen und hinzufügen bzw. Bestand und Kursalarm bearbeiten."""

    def __init__(self, app: App, coin: dict | None = None):
        super().__init__(app, "Coin bearbeiten" if coin else "Coin hinzufügen")
        self.coin = coin
        self.found: dict[str, dict] = {}
        self.selected: dict | None = None
        cfg = app.store.get()
        self.cfg = cfg
        body = self.body
        body.columnconfigure(0, weight=1)
        row = 0
        focus = None
        if coin is None:
            ttk.Label(body, text="Datenquelle", style="Bold.TLabel").grid(row=row, column=0, sticky="w")
            self.source = tk.StringVar(value="coingecko")
            ttk.Radiobutton(body, text="CoinGecko – Durchschnittskurs über viele Börsen (empfohlen)",
                            variable=self.source, value="coingecko", command=self._source_changed
                            ).grid(row=row + 1, column=0, sticky="w")
            ttk.Radiobutton(body, text="Binance – Kurs direkt von der Börse (Handelspaar wie BTCEUR oder ETHUSDT)",
                            variable=self.source, value="binance", command=self._source_changed
                            ).grid(row=row + 2, column=0, sticky="w")
            search = ttk.Frame(body)
            search.grid(row=row + 3, column=0, sticky="ew", pady=(12, 6))
            self.query = tk.StringVar()
            entry = ttk.Entry(search, textvariable=self.query, width=40)
            entry.pack(side="left", fill="x", expand=True)
            entry.bind("<Return>", lambda event: self.search())
            ttk.Button(search, text="Suchen", command=self.search).pack(side="left", padx=(6, 0))
            columns = [("name", "Name", 220, "w", True), ("symbol", "Kürzel", 80, "w", False),
                       ("info", "Rang / Kurs", 130, "e", False), ("id", "ID / Handelspaar", 170, "w", False)]
            tree_frame, self.results = make_tree(body, columns, height=7, scale=app.scale)
            tree_frame.grid(row=row + 4, column=0, sticky="nsew")
            self.results.bind("<<TreeviewSelect>>", self._on_select)
            self.results.bind("<Double-1>", lambda event: self.save())
            self.search_info = ttk.Label(body, text="Name oder Kürzel eingeben (z. B. „bitcoin“ oder „SOL“) und "
                                                    "„Suchen“ klicken.", style="Muted.TLabel", wraplength=app.px(600))
            self.search_info.grid(row=row + 5, column=0, sticky="w", pady=(4, 0))
            row += 6
            focus = entry
        else:
            ttk.Label(body, text=coin_label(coin), style="Title.TLabel").grid(row=row, column=0, sticky="w")
            kind = "CoinGecko-ID" if coin["source"] == "coingecko" else "Handelspaar"
            ttk.Label(body, text=f"Quelle: {SOURCE_LABELS[coin['source']]} · {kind}: {coin['id']}",
                      style="Muted.TLabel").grid(row=row + 1, column=0, sticky="w")
            row += 2

        currency = coin_currency(coin, cfg) if coin else cfg["currency"].upper()
        holding = ttk.LabelFrame(body, text="Bestand (optional – für Gewinn/Verlust)", padding=10)
        holding.grid(row=row, column=0, sticky="ew", pady=(14, 0))
        self.amount, amount_entry, _ = self.number_field(holding, 0, "Menge (Anzahl Coins):",
                                                         coin["amount"] if coin else 0, amount=True)
        self.buy, _, buy_label = self.number_field(holding, 1, "", coin["buy_price"] if coin else 0)
        alarms = ttk.LabelFrame(body, text="Kursalarm (optional)", padding=10)
        alarms.grid(row=row + 1, column=0, sticky="ew", pady=(10, 0))
        self.above, _, above_label = self.number_field(alarms, 0, "", coin["alarm_above"] if coin else None)
        self.below, _, below_label = self.number_field(alarms, 1, "", coin["alarm_below"] if coin else None)
        self.currency_labels = [(buy_label, "Kaufpreis je Coin"), (above_label, "Melden, wenn der Kurs steigt über"),
                                (below_label, "Melden, wenn der Kurs fällt unter")]
        self._set_currency(currency)
        buttons = ttk.Frame(body)
        buttons.grid(row=row + 2, column=0, sticky="e", pady=(16, 0))
        ttk.Button(buttons, text="Abbrechen", command=self.destroy).pack(side="right")
        ttk.Button(buttons, text="Speichern", command=self.save).pack(side="right", padx=6)
        self.show(focus or amount_entry)

    def _set_currency(self, currency: str) -> None:
        """Beschriftungen zeigen, in welcher Währung die Beträge gemeint sind."""
        for label, text in self.currency_labels:
            label.configure(text=f"{text} ({currency or 'Kurswährung'}):")

    def _source_changed(self) -> None:
        self.results.delete(*self.results.get_children())
        self.found, self.selected = {}, None
        if self.source.get() == "binance":
            self.search_info.configure(text="Coin-Kürzel eingeben (z. B. „BTC“), dann das Handelspaar wählen – "
                                            "z. B. BTCEUR für Euro oder BTCUSDT für US-Dollar-Stablecoin.")
        else:
            self.search_info.configure(text="Name oder Kürzel eingeben (z. B. „bitcoin“ oder „SOL“) und „Suchen“ klicken.")
        self._set_currency("" if self.source.get() == "binance" else self.cfg["currency"].upper())

    def search(self) -> None:
        query = self.query.get().strip()
        if len(query) < 2:
            self.search_info.configure(text="Bitte mindestens 2 Zeichen eingeben.")
            return
        source = self.source.get()
        self.results.delete(*self.results.get_children())
        self.found, self.selected = {}, None
        self.search_info.configure(text="Suche läuft …")
        run_in_background(self.app.root, lambda: self.app.monitor.search(source, query), self._show_results, owner=self)

    def _show_results(self, results, error) -> None:
        if error:
            self.search_info.configure(text=f"Suche fehlgeschlagen: {error}\nDu kannst die ID bzw. das Handelspaar "
                                            "auch direkt eintippen und auf „Speichern“ klicken.")
            return
        if not results:
            self.search_info.configure(text="Nichts gefunden – andere Schreibweise versuchen (meist der englische Name).")
            return
        for index, item in enumerate(results):
            iid = str(index)
            self.found[iid] = item
            self.results.insert("", "end", iid=iid, values=(item["name"], item["symbol"], item.get("info", ""), item["id"]))
        self.search_info.configure(text=f"{len(results)} Treffer – Coin auswählen und „Speichern“ klicken.")
        self.results.selection_set("0")
        self.results.focus("0")

    def _on_select(self, event=None) -> None:
        selection = self.results.selection()
        self.selected = self.found.get(selection[0]) if selection else None
        if self.selected and self.selected["source"] == "binance":
            self._set_currency(split_symbol(self.selected["id"])[1])

    def save(self) -> None:
        try:
            amount = self.read_number(self.amount, "Menge") or 0.0
            buy = self.read_number(self.buy, "Kaufpreis") or 0.0
            above = self.read_number(self.above, "Kursalarm über")
            below = self.read_number(self.below, "Kursalarm unter")
        except ValueError as exc:
            messagebox.showerror("Ungültige Eingabe", str(exc), parent=self)
            return
        if above and below and below >= above:
            messagebox.showerror("Ungültige Eingabe", "Der Wert bei „fällt unter“ muss kleiner sein als bei "
                                                      "„steigt über“.", parent=self)
            return
        if self.coin is None:
            choice = self.selected
            if choice is None:
                text = self.query.get().strip()
                if not text:
                    messagebox.showinfo(APP_NAME, "Bitte zuerst einen Coin suchen und in der Liste auswählen.", parent=self)
                    return
                kind = "CoinGecko-ID" if self.source.get() == "coingecko" else "Binance-Handelspaar"
                if not messagebox.askyesno("Ohne Suchergebnis übernehmen?",
                                           f"„{text}“ direkt als {kind} übernehmen?\n\nTipp: Mit „Suchen“ findest du "
                                           "die richtige Schreibweise.", parent=self):
                    return
                choice = {"source": self.source.get(), "id": text, "name": "", "symbol": ""}
            coin = {key: choice.get(key, "") for key in ("source", "id", "name", "symbol")}
        else:
            coin = dict(self.coin)
        coin.update(amount=amount, buy_price=buy, alarm_above=above, alarm_below=below)
        self.result = coin
        self.destroy()


class SettingsDialog(Dialog):
    def __init__(self, app: App):
        super().__init__(app, "Einstellungen")
        cfg = app.store.get()
        self.cfg = cfg
        notebook = ttk.Notebook(self.body)
        notebook.pack(fill="both", expand=True)
        notebook.add(self._general_tab(notebook, cfg), text="Allgemein")
        notebook.add(self._alarm_tab(notebook, cfg), text="Alarme")
        notebook.add(self._notification_tab(notebook, cfg), text="Benachrichtigungen")
        buttons = ttk.Frame(self.body, padding=(0, 12, 0, 0))
        buttons.pack(fill="x")
        ttk.Button(buttons, text="Abbrechen", command=self.destroy).pack(side="right")
        ttk.Button(buttons, text="Speichern", command=self.save).pack(side="right", padx=6)
        self.show()

    def _hint(self, parent, text: str, row: int, column: int = 0, columnspan: int = 3) -> None:
        ttk.Label(parent, text=text, style="Muted.TLabel", wraplength=self.app.px(560), justify="left").grid(
            row=row, column=column, columnspan=columnspan, sticky="w", pady=(0, 8))

    def _general_tab(self, notebook, cfg: dict):
        frame = ttk.Frame(notebook, padding=14)
        self.currency = tk.StringVar(value=cfg["currency"])
        ttk.Label(frame, text="Währung:").grid(row=0, column=0, sticky="w", pady=3)
        ttk.Combobox(frame, textvariable=self.currency, values=CURRENCIES, width=8).grid(row=0, column=1, sticky="w")
        self._hint(frame, "Gilt für CoinGecko-Kurse. Kaufpreise und Kursalarme trägst du in dieser Währung ein. "
                          "Binance-Kurse haben die Währung ihres Handelspaars (z. B. BTCEUR = Euro).", 1)
        self.interval = tk.StringVar(value=str(cfg["poll_interval"]))
        ttk.Label(frame, text="Aktualisieren alle (Sekunden):").grid(row=2, column=0, sticky="w", pady=3)
        ttk.Spinbox(frame, from_=20, to=3600, increment=10, textvariable=self.interval, width=8).grid(
            row=2, column=1, sticky="w")
        self._hint(frame, "Ohne API-Key erlaubt CoinGecko nur wenige Abrufe pro Minute – 60 Sekunden sind ein guter Wert.", 3)
        self.api_key = tk.StringVar(value=cfg["coingecko_api_key"])
        ttk.Label(frame, text="CoinGecko-API-Key (optional):").grid(row=4, column=0, sticky="w", pady=3)
        ttk.Entry(frame, textvariable=self.api_key, width=40).grid(row=4, column=1, columnspan=2, sticky="w")
        link = ttk.Label(frame, text="Kostenlosen „Demo“-Key bei CoinGecko erstellen (nur nötig bei vielen Abrufen)",
                         foreground="#1d4ed8", cursor="hand2")
        link.grid(row=5, column=0, columnspan=3, sticky="w", pady=(0, 8))
        link.bind("<Button-1>", lambda event: webbrowser.open(COINGECKO_API_URL))
        self.autostart = tk.BooleanVar(value=cfg["autostart"])
        check = ttk.Checkbutton(frame, text="Mit Windows starten (minimiert in der Taskleiste)", variable=self.autostart)
        check.grid(row=6, column=0, columnspan=3, sticky="w", pady=(6, 3))
        if not winutils.IS_WINDOWS:
            check.state(["disabled"])
        ttk.Label(frame, text=f"Einstellungen und Protokolle: {self.app.store.folder}", style="Muted.TLabel",
                  wraplength=self.app.px(560)).grid(row=7, column=0, columnspan=2, sticky="w", pady=(14, 0))
        ttk.Button(frame, text="Ordner öffnen", command=self.app.open_folder).grid(row=7, column=2, sticky="e",
                                                                                   pady=(14, 0))
        return frame

    def _alarm_tab(self, notebook, cfg: dict):
        frame = ttk.Frame(notebook, padding=14)
        moves = ttk.LabelFrame(frame, text="Starke Kursbewegung (Coins der Watchlist)", padding=10)
        moves.grid(row=0, column=0, sticky="ew")
        self._hint(moves, "Alarm, wenn ein Coin innerhalb des Zeitraums mindestens so stark steigt oder fällt:", 0)
        rules = ttk.Frame(moves)
        rules.grid(row=1, column=0, sticky="w")
        self.rule_vars = []
        for index, rule in enumerate(cfg["move_rules"]):
            enabled = tk.BooleanVar(value=rule["enabled"])
            percent = tk.StringVar(value=format_plain(rule["percent"]))
            ttk.Checkbutton(rules, text=f"in {window_label(rule['minutes'])}", variable=enabled, width=12).grid(
                row=index, column=0, sticky="w")
            ttk.Label(rules, text="ab ±").grid(row=index, column=1, sticky="e", padx=(12, 0))
            ttk.Entry(rules, textvariable=percent, width=7, justify="right").grid(row=index, column=2, padx=4, pady=2)
            ttk.Label(rules, text="%").grid(row=index, column=3, sticky="w")
            self.rule_vars.append((rule["minutes"], enabled, percent))
        repeat = ttk.Frame(moves)
        repeat.grid(row=2, column=0, sticky="w", pady=(8, 0))
        self.cooldown = tk.StringVar(value=str(cfg["cooldown_minutes"]))
        ttk.Label(repeat, text="Gleichen Alarm frühestens nach").pack(side="left")
        ttk.Spinbox(repeat, from_=0, to=1440, increment=5, textvariable=self.cooldown, width=6).pack(side="left", padx=4)
        ttk.Label(repeat, text="Minuten wiederholen (wird es noch stärker, sofort)").pack(side="left")

        scan = cfg["scanner"]
        scanner = ttk.LabelFrame(frame, text="Markt-Scanner (die größten Coins, ohne Watchlist)", padding=10)
        scanner.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        self.scan_enabled = tk.BooleanVar(value=scan["enabled"])
        self.scan_top = tk.StringVar(value=str(scan["top_n"]))
        self.scan_1h = tk.StringVar(value=format_plain(scan["percent_1h"]))
        self.scan_24h = tk.StringVar(value=format_plain(scan["percent_24h"]))
        line = ttk.Frame(scanner)
        line.grid(row=0, column=0, sticky="w")
        ttk.Checkbutton(line, text="Die größten", variable=self.scan_enabled).pack(side="left")
        ttk.Spinbox(line, from_=10, to=250, increment=10, textvariable=self.scan_top, width=5).pack(side="left", padx=4)
        ttk.Label(line, text="Coins überwachen").pack(side="left")
        line = ttk.Frame(scanner)
        line.grid(row=1, column=0, sticky="w", pady=(6, 0))
        ttk.Label(line, text="Alarm ab ±").pack(side="left")
        ttk.Entry(line, textvariable=self.scan_1h, width=6, justify="right").pack(side="left", padx=4)
        ttk.Label(line, text="% in 1 Std   oder ab ±").pack(side="left")
        ttk.Entry(line, textvariable=self.scan_24h, width=6, justify="right").pack(side="left", padx=4)
        ttk.Label(line, text="% in 24 Std   (0 = aus)").pack(side="left")

        pf = cfg["portfolio"]
        portfolio = ttk.LabelFrame(frame, text="Portfolio (Coins mit eingetragenem Bestand)", padding=10)
        portfolio.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        self.profit = tk.StringVar(value=format_plain(pf["profit_percent"]))
        self.loss = tk.StringVar(value=format_plain(pf["loss_percent"]))
        self.pf_move = tk.StringVar(value=format_plain(pf["move_percent"]))
        labels = [label for _, label in PORTFOLIO_WINDOWS]
        current = next((label for minutes, label in PORTFOLIO_WINDOWS if minutes == pf["move_minutes"]),
                       window_label(pf["move_minutes"]))
        self.pf_window = tk.StringVar(value=current)
        rows = [("Gewinn seit Kauf ab +", self.profit, "% melden (dann erneut bei jedem weiteren Schritt)"),
                ("Verlust seit Kauf ab −", self.loss, "% melden"),
                ("Gesamtwert bewegt sich um ±", self.pf_move, "%")]
        for index, (text, var, suffix) in enumerate(rows):
            ttk.Label(portfolio, text=text).grid(row=index, column=0, sticky="w", pady=2)
            ttk.Entry(portfolio, textvariable=var, width=7, justify="right").grid(row=index, column=1, padx=4)
            ttk.Label(portfolio, text=suffix).grid(row=index, column=2, sticky="w")
        window = ttk.Frame(portfolio)
        window.grid(row=2, column=2, sticky="w", padx=(18, 0))
        ttk.Label(window, text="in").pack(side="left")
        ttk.Combobox(window, textvariable=self.pf_window, values=labels, width=8, state="readonly").pack(side="left", padx=4)
        self._hint(portfolio, "0 schaltet den jeweiligen Alarm aus.", 3)
        return frame

    def _notification_tab(self, notebook, cfg: dict):
        frame = ttk.Frame(notebook, padding=14)
        settings = cfg["notifications"]
        self.toast = tk.BooleanVar(value=settings["toast"])
        self.compat = tk.BooleanVar(value=settings["toast_compat"])
        self.sound = tk.BooleanVar(value=settings["sound"])
        self.flash = tk.BooleanVar(value=settings["flash"])
        ttk.Checkbutton(frame, text="Windows-Benachrichtigung anzeigen", variable=self.toast).grid(
            row=0, column=0, columnspan=3, sticky="w")
        ttk.Checkbutton(frame, text="Kompatibilitätsmodus (Absender „Windows PowerShell“) – nur nötig, wenn keine "
                                    "Meldungen erscheinen", variable=self.compat).grid(row=1, column=0, columnspan=3,
                                                                                       sticky="w", padx=(22, 0))
        ttk.Checkbutton(frame, text="Ton abspielen", variable=self.sound).grid(row=2, column=0, columnspan=3, sticky="w")
        ttk.Checkbutton(frame, text="Taskleistensymbol blinken lassen", variable=self.flash).grid(
            row=3, column=0, columnspan=3, sticky="w")

        telegram = ttk.LabelFrame(frame, text="Telegram – Alarme aufs Handy (optional)", padding=10)
        telegram.grid(row=4, column=0, columnspan=3, sticky="ew", pady=(14, 0))
        self.tg_enabled = tk.BooleanVar(value=settings["telegram_enabled"])
        self.tg_token = tk.StringVar(value=settings["telegram_token"])
        self.tg_chat = tk.StringVar(value=settings["telegram_chat_id"])
        ttk.Checkbutton(telegram, text="Alarme zusätzlich per Telegram senden", variable=self.tg_enabled).grid(
            row=0, column=0, columnspan=3, sticky="w")
        ttk.Label(telegram, text="Bot-Token:").grid(row=1, column=0, sticky="w", pady=3)
        ttk.Entry(telegram, textvariable=self.tg_token, width=50).grid(row=1, column=1, columnspan=2, sticky="w")
        ttk.Label(telegram, text="Chat-ID:").grid(row=2, column=0, sticky="w", pady=3)
        chat = ttk.Frame(telegram)
        chat.grid(row=2, column=1, columnspan=2, sticky="w")
        ttk.Entry(chat, textvariable=self.tg_chat, width=20).pack(side="left")
        ttk.Button(chat, text="Chat-ID ermitteln", command=self.find_chat).pack(side="left", padx=6)
        ttk.Button(telegram, text="Test-Nachricht senden", command=self.test_telegram).grid(
            row=3, column=1, sticky="w", pady=(6, 0))
        self.tg_info = ttk.Label(telegram, text="", style="Muted.TLabel", wraplength=self.app.px(540))
        self.tg_info.grid(row=4, column=0, columnspan=3, sticky="w", pady=(6, 0))
        self._hint(telegram, "So geht's: 1. In Telegram den Kontakt @BotFather öffnen, /newbot senden und den "
                             "erhaltenen Token oben eintragen. 2. Deinem neuen Bot eine beliebige Nachricht schicken. "
                             "3. „Chat-ID ermitteln“ klicken.", 5)
        return frame

    def find_chat(self) -> None:
        token = self.tg_token.get()
        self.tg_info.configure(text="Suche Chat-ID …", foreground=MUTED)

        def done(chat_id, error):
            if error:
                self.tg_info.configure(text=str(error), foreground=RED)
            elif not chat_id:
                self.tg_info.configure(text="Keine Nachricht gefunden – schreibe deinem Bot zuerst eine Nachricht "
                                            "und klicke dann erneut.", foreground=RED)
            else:
                self.tg_chat.set(chat_id)
                self.tg_info.configure(text=f"Chat-ID gefunden: {chat_id}", foreground=GREEN)

        run_in_background(self.app.root, lambda: find_telegram_chat_id(token), done, owner=self)

    def test_telegram(self) -> None:
        token, chat = self.tg_token.get(), self.tg_chat.get()
        self.tg_info.configure(text="Sende Test-Nachricht …", foreground=MUTED)

        def done(_, error):
            if error:
                self.tg_info.configure(text=str(error), foreground=RED)
            else:
                self.tg_info.configure(text="Test-Nachricht gesendet – schau in Telegram nach.", foreground=GREEN)

        run_in_background(self.app.root, lambda: send_telegram(
            token, chat, "✅ Test vom Krypto-Wächter – die Telegram-Benachrichtigung funktioniert."), done, owner=self)

    @staticmethod
    def _integer(var, label: str, minimum: int, maximum: int) -> int:
        try:
            value = int(float(var.get().strip().replace(",", ".")))
        except ValueError:
            raise ValueError(f"„{var.get()}“ ist bei „{label}“ keine gültige Zahl.") from None
        if not minimum <= value <= maximum:
            raise ValueError(f"„{label}“ muss zwischen {minimum} und {maximum} liegen.")
        return value

    def save(self) -> None:
        try:
            interval = self._integer(self.interval, "Aktualisieren alle", 20, 3600)
            cooldown = self._integer(self.cooldown, "Alarm wiederholen nach", 0, 1440)
            top_n = self._integer(self.scan_top, "Markt-Scanner", 10, 250)
            rules = []
            for minutes, enabled, percent in self.rule_vars:
                label = f"Kursbewegung in {window_label(minutes)}"
                value = self.read_number(percent, label, minimum=0.1)
                if value is None:
                    raise ValueError(f"Bitte bei „{label}“ einen Prozentwert eintragen.")
                rules.append({"minutes": minutes, "enabled": enabled.get(), "percent": value})
            scan_1h = self.read_number(self.scan_1h, "Markt-Scanner 1 Std") or 0.0
            scan_24h = self.read_number(self.scan_24h, "Markt-Scanner 24 Std") or 0.0
            profit = self.read_number(self.profit, "Gewinn seit Kauf") or 0.0
            loss = self.read_number(self.loss, "Verlust seit Kauf") or 0.0
            pf_move = self.read_number(self.pf_move, "Gesamtwert bewegt sich") or 0.0
        except ValueError as exc:
            messagebox.showerror("Ungültige Eingabe", str(exc), parent=self)
            return
        currency = self.currency.get().strip().lower()
        if not re.fullmatch(r"[a-z]{3,5}", currency):
            messagebox.showerror("Ungültige Eingabe", "Bitte eine Währung wie „eur“ oder „usd“ angeben.", parent=self)
            return
        if loss >= 100:
            messagebox.showerror("Ungültige Eingabe", "Der Verlust-Alarm muss unter 100 % liegen.", parent=self)
            return
        if self.tg_enabled.get() and not (self.tg_token.get().strip() and self.tg_chat.get().strip()):
            messagebox.showerror("Telegram", "Für Telegram bitte Bot-Token und Chat-ID eintragen.", parent=self)
            return
        move_minutes = next((m for m, label in PORTFOLIO_WINDOWS if label == self.pf_window.get()),
                            self.cfg["portfolio"]["move_minutes"])
        values = {
            "currency": currency,
            "poll_interval": interval,
            "coingecko_api_key": self.api_key.get().strip(),
            "move_rules": rules,
            "cooldown_minutes": cooldown,
            "scanner": {"enabled": self.scan_enabled.get(), "top_n": top_n, "percent_1h": scan_1h,
                        "percent_24h": scan_24h},
            "portfolio": {"profit_percent": profit, "loss_percent": loss, "move_percent": pf_move,
                          "move_minutes": move_minutes},
            "notifications": {
                "toast": self.toast.get(), "toast_compat": self.compat.get(), "sound": self.sound.get(),
                "flash": self.flash.get(), "telegram_enabled": self.tg_enabled.get(),
                "telegram_token": self.tg_token.get().strip(), "telegram_chat_id": self.tg_chat.get().strip(),
            },
            "autostart": self.autostart.get(),
        }
        self.result = lambda cfg: cfg.update(values)
        self.destroy()


def run_gui(store, notifier, demo: bool = False, minimized: bool = False, interval: int | None = None,
            alert_log: Path | None = None) -> int:
    winutils.enable_dpi_awareness()
    winutils.set_process_app_id(APP_ID)
    if winutils.IS_WINDOWS and store.get()["autostart"]:
        try:
            winutils.set_autostart(True)  # Pfad aktuell halten, falls das Programm verschoben wurde
        except OSError as exc:
            log.warning("Autostart konnte nicht aktualisiert werden: %s", exc)
    root = tk.Tk()

    def report_error(exc_type, value, traceback):
        log.error("Unerwarteter Fehler", exc_info=(exc_type, value, traceback))
        messagebox.showerror(APP_NAME, f"Unerwarteter Fehler: {value}\n\nDetails stehen in krypto-waechter.log.",
                             parent=root)

    root.report_callback_exception = report_error
    App(root, store, notifier, demo=demo, interval=interval, alert_log=alert_log)
    if minimized:
        root.iconify()
    root.mainloop()
    return 0
