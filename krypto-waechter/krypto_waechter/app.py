"""Programmstart: Befehlszeile auswerten und Fenster bzw. Konsole starten."""

from __future__ import annotations

import argparse
import logging
import logging.handlers
import shutil
import sys
import tempfile
from pathlib import Path

from . import APP_ID, APP_NAME, __version__
from .config import ConfigStore, default_config_dir

log = logging.getLogger("krypto_waechter")
ASSETS = Path(__file__).resolve().parent / "assets"


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="Krypto-Waechter",
        description=f"{APP_NAME}: überwacht Kryptowährungen und meldet starke Kursbewegungen.",
    )
    parser.add_argument("--konsole", action="store_true", help="ohne Fenster im Konsolenmodus laufen")
    parser.add_argument("--demo", action="store_true", help="simulierte Kurse zum Ausprobieren (kein Internet nötig)")
    parser.add_argument("--einmal", action="store_true", help="nur eine Aktualisierung durchführen (Konsolenmodus)")
    parser.add_argument("--minimiert", action="store_true", help="minimiert starten (für den Autostart)")
    parser.add_argument("--intervall", type=int, metavar="SEK", help="Abrufintervall in Sekunden (überschreibt die Einstellung)")
    parser.add_argument("--config", metavar="DATEI", help="andere Konfigurationsdatei verwenden")
    parser.add_argument("--selbsttest", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--version", action="version", version=f"{APP_NAME} {__version__}")
    return parser.parse_args(argv)


def setup_logging(folder: Path) -> None:
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    try:
        folder.mkdir(parents=True, exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(folder / "krypto-waechter.log", maxBytes=1_000_000,
                                                       backupCount=2, encoding="utf-8")
    except OSError:
        return
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root.addHandler(handler)


def install_icon(folder: Path) -> Path | None:
    """Symbol an einen festen Ort kopieren – Windows zeigt es in den Benachrichtigungen an."""
    source, target = ASSETS / "krypto-waechter.png", folder / "krypto-waechter.png"
    try:
        if source.exists() and (not target.exists() or target.stat().st_size != source.stat().st_size):
            folder.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
        return target if target.exists() else None
    except OSError:
        return None


class _SilentNotifier:
    def send(self, alerts) -> None:
        pass


def selftest() -> int:
    """Prüft, ob alle Bestandteile funktionieren (wird nach dem Bauen der .exe aufgerufen)."""
    from .monitor import Monitor
    from .notifier import build_toast_script, build_toast_xml, run_powershell

    errors = []
    with tempfile.TemporaryDirectory() as folder:
        store = ConfigStore(Path(folder) / "config.json")
        snapshots = []
        monitor = Monitor(store, _SilentNotifier(), snapshots.append, demo=True)
        for _ in range(12):
            monitor.run_cycle()
        if not any(snapshot.alerts for snapshot in snapshots):
            errors.append("Demo-Modus hat keine Alarme erzeugt")
    if sys.platform == "win32":
        script = build_toast_script(build_toast_xml("Selbsttest äöü € 📈", "Zeile 1\nZeile 2"), APP_ID, show=False)
        ok, error = run_powershell(script)
        if not ok:
            errors.append(f"Windows-Benachrichtigung: {error}")
    try:
        import tkinter

        root = tkinter.Tk()
        root.withdraw()
        root.update_idletasks()
        root.destroy()
    except Exception as exc:
        errors.append(f"Tkinter: {exc}")
    for error in errors:
        log.error("Selbsttest: %s", error)
        print(f"FEHLER: {error}")
    if not errors:
        print("Selbsttest erfolgreich")
    return 1 if errors else 0


def demo_config(folder: Path) -> Path:
    """Eigene Einstellungen für den Demo-Modus – beim ersten Mal eine Kopie der echten."""
    path = folder / "demo" / "config.json"
    if not path.exists() and (folder / "config.json").exists():
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(folder / "config.json", path)
        except OSError:
            pass
    return path


def main(argv=None) -> int:
    args = parse_args(argv)
    if args.config:
        config_path = Path(args.config).expanduser()
    elif args.demo:
        config_path = demo_config(default_config_dir())
    else:
        config_path = default_config_dir() / "config.json"
    setup_logging(config_path.parent)
    log.info("%s %s gestartet %s", APP_NAME, __version__, " ".join(sys.argv[1:]))
    if args.selbsttest:
        return selftest()

    from .notifier import Notifier

    store = ConfigStore(config_path)
    notifier = Notifier(store, icon_path=install_icon(store.folder))
    alert_log = None if args.demo else store.folder / "alarme.csv"
    if args.konsole:
        from .console import run_console

        return run_console(store, notifier, demo=args.demo, once=args.einmal, interval=args.intervall,
                           alert_log=alert_log)

    from . import winutils

    if not args.demo and not winutils.acquire_single_instance(APP_ID):
        import tkinter
        from tkinter import messagebox

        root = tkinter.Tk()
        root.withdraw()
        messagebox.showinfo(APP_NAME, f"{APP_NAME} läuft bereits – das Fenster findest du in der Taskleiste.")
        root.destroy()
        return 0

    from .gui import run_gui

    return run_gui(store, notifier, demo=args.demo, minimized=args.minimiert, interval=args.intervall,
                   alert_log=alert_log)
