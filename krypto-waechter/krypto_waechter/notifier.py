"""Benachrichtigungen: Windows-Meldungen, Ton und (optional) Telegram."""

from __future__ import annotations

import base64
import logging
import os
import queue
import re
import subprocess
import sys
import threading
from pathlib import Path
from xml.sax.saxutils import escape

from . import APP_ID, APP_NAME
from .net import NetError, request_json

log = logging.getLogger(__name__)

# App-Kennung von Windows PowerShell – vorhanden auf jedem Windows 10/11 (Kompatibilitätsmodus)
POWERSHELL_APP_ID = r"{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe"
# Bei mehr Alarmen auf einmal gibt es eine Sammelmeldung statt vieler einzelner
MAX_SINGLE_TOASTS = 3
TELEGRAM_API = "https://api.telegram.org/bot{token}/{method}"


# ---------------------------------------------------------------------------
# Windows-Meldungen (Toasts) über PowerShell – ohne Zusatzpakete
# ---------------------------------------------------------------------------

def _xml_text(text: str) -> str:
    """XML-Text nur aus ASCII-Zeichen: Umlaute und Emojis werden zu &#…;.

    Dadurch lässt sich der Text gefahrlos in ein PowerShell-Skript einbetten.
    """
    escaped = escape(text, {'"': "&quot;", "'": "&apos;"})
    return "".join(
        ch if 32 <= ord(ch) < 127 or ch in "\n\t" else f"&#{ord(ch)};"
        for ch in escaped
        if ord(ch) >= 32 or ch in "\n\t"
    )


def build_toast_xml(title: str, message: str) -> str:
    lines = [line.strip() for line in message.split("\n") if line.strip()]
    if len(lines) > 2:
        lines = [lines[0], " · ".join(lines[1:])]
    texts = "".join(f"<text>{_xml_text(text)}</text>" for text in [title, *lines])
    return ('<toast duration="long"><visual><binding template="ToastGeneric">'
            f"{texts}</binding></visual><audio silent=\"true\"/></toast>")


def _ps_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def build_toast_script(xml: str, app_id: str, show: bool = True) -> str:
    """PowerShell-Skript, das die Meldung über die Windows-Runtime anzeigt."""
    lines = [
        "$ErrorActionPreference = 'Stop'",
        "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null",
        "[Windows.UI.Notifications.ToastNotification, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null",
        "[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null",
        "$xml = New-Object Windows.Data.Xml.Dom.XmlDocument",
        f"$xml.LoadXml({_ps_quote(xml)})",
        "$toast = [Windows.UI.Notifications.ToastNotification]::new($xml)",
    ]
    if show:
        lines.append("[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("
                     f"{_ps_quote(app_id)}).Show($toast)")
    return "\n".join(lines)


def encode_powershell(script: str) -> str:
    """Kodierung für 'powershell -EncodedCommand' (Base64 von UTF-16LE)."""
    return base64.b64encode(script.encode("utf-16-le")).decode("ascii")


def _powershell_exe() -> str:
    path = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    return str(path) if path.exists() else "powershell.exe"


def _clean_error(text: str) -> str:
    # Fehler kommen bei -EncodedCommand oft im CLIXML-Format
    parts = re.findall(r'<S S="Error">(.*?)</S>', text)
    if parts:
        text = " ".join(parts)
    text = text.replace("_x000D_", "").replace("_x000A_", " ")
    return " ".join(text.split())[:300]


def run_powershell(script: str, timeout: float = 30.0) -> tuple[bool, str]:
    """Führt ein PowerShell-Skript ohne sichtbares Fenster aus."""
    command = [_powershell_exe(), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
               "-EncodedCommand", encode_powershell(script)]
    try:
        result = subprocess.run(command, capture_output=True, timeout=timeout, stdin=subprocess.DEVNULL,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError) as exc:
        return False, str(exc)
    if result.returncode != 0:
        output = (result.stderr or result.stdout).decode("utf-8", "replace")
        return False, _clean_error(output) or f"Exit-Code {result.returncode}"
    return True, ""


def show_toast(title: str, message: str, app_id: str) -> tuple[bool, str]:
    return run_powershell(build_toast_script(build_toast_xml(title, message), app_id))


def register_app_id(app_id: str, display_name: str, icon_path: Path | None) -> None:
    """Meldet das Programm bei Windows als Absender von Benachrichtigungen an (Name und Symbol)."""
    import winreg

    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, rf"Software\Classes\AppUserModelId\{app_id}", 0,
                            winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, "DisplayName", 0, winreg.REG_SZ, display_name)
        if icon_path and Path(icon_path).exists():
            winreg.SetValueEx(key, "IconUri", 0, winreg.REG_SZ, str(icon_path))


def play_sound(direction: str) -> None:
    if sys.platform != "win32":
        return
    import winsound

    alias = "SystemHand" if direction == "down" else "SystemAsterisk"
    winsound.PlaySound(alias, winsound.SND_ALIAS | winsound.SND_ASYNC)


# ---------------------------------------------------------------------------
# Telegram (optional, Meldungen aufs Handy)
# ---------------------------------------------------------------------------

def _telegram_error(exc: NetError) -> str:
    text = str(exc)
    if exc.status == 401 or exc.status == 404:
        return "Bot-Token ist ungültig"
    if "chat not found" in text.lower():
        return "Chat-ID nicht gefunden – schreibe deinem Bot zuerst eine Nachricht"
    if exc.status == 403:
        return "der Bot darf dir nicht schreiben (blockiert oder nie gestartet)"
    return text


def send_telegram(token: str, chat_id: str, text: str, request=request_json) -> None:
    token, chat_id = token.strip(), str(chat_id).strip()
    if not token or not chat_id:
        raise NetError("Telegram: Bot-Token und Chat-ID eintragen")
    try:
        request(TELEGRAM_API.format(token=token, method="sendMessage"),
                data={"chat_id": chat_id, "text": text[:4000], "disable_web_page_preview": True})
    except NetError as exc:
        raise NetError(f"Telegram: {_telegram_error(exc)}", status=exc.status) from None


def find_telegram_chat_id(token: str, request=request_json) -> str | None:
    """Chat-ID aus der letzten Nachricht, die jemand an den Bot geschickt hat."""
    token = token.strip()
    if not token:
        raise NetError("Telegram: zuerst den Bot-Token eintragen")
    try:
        data = request(TELEGRAM_API.format(token=token, method="getUpdates"))
    except NetError as exc:
        raise NetError(f"Telegram: {_telegram_error(exc)}", status=exc.status) from None
    updates = data.get("result") if isinstance(data, dict) else None
    for update in reversed(updates or []):
        for field in ("message", "edited_message", "channel_post", "my_chat_member"):
            chat = (update.get(field) or {}).get("chat") if isinstance(update, dict) else None
            if isinstance(chat, dict) and "id" in chat:
                return str(chat["id"])
    return None


def telegram_text(alerts: list) -> str:
    return "\n\n".join(f"{a.icon} {a.title}\n{a.message}".strip() for a in alerts)[:4000]


def alert_heading(alert) -> str:
    return f"{alert.icon} {alert.title}".strip()


# ---------------------------------------------------------------------------

class Notifier:
    """Stellt Alarme im Hintergrund zu, damit Programm und Oberfläche nicht warten müssen."""

    def __init__(self, store, icon_path: Path | None = None):
        self.store = store
        self.icon_path = icon_path
        self._app_id_checked: str | None = None
        self._queue: queue.Queue = queue.Queue()
        self._thread = threading.Thread(target=self._worker, name="Benachrichtigungen", daemon=True)
        self._thread.start()

    def send(self, alerts: list) -> None:
        if alerts:
            self._queue.put((list(alerts), None))

    def flush(self, timeout: float = 60.0) -> bool:
        """Wartet, bis alle bisher übergebenen Alarme zugestellt sind."""
        done = threading.Event()
        self._queue.put((None, done))
        return done.wait(timeout)

    def _worker(self) -> None:
        while True:
            alerts, done = self._queue.get()
            try:
                if alerts:
                    self.deliver(alerts)
            except Exception:
                log.exception("Benachrichtigung fehlgeschlagen")
            finally:
                if done is not None:
                    done.set()

    def deliver(self, alerts: list) -> dict[str, str]:
        """Stellt Alarme über alle eingeschalteten Wege zu. Ergebnis je Weg: '' = ok, sonst Fehlertext."""
        settings = self.store.get()["notifications"]
        results = {}
        if settings["toast"] and sys.platform == "win32":
            results["Windows"] = self._toasts(alerts, settings["toast_compat"])
        if settings["sound"] and sys.platform == "win32":
            try:
                play_sound("down" if any(a.direction == "down" for a in alerts) else "up")
                results["Ton"] = ""
            except Exception as exc:  # z. B. keine Soundkarte
                results["Ton"] = str(exc)
        if settings["telegram_enabled"]:
            try:
                send_telegram(settings["telegram_token"], settings["telegram_chat_id"], telegram_text(alerts))
                results["Telegram"] = ""
            except NetError as exc:
                results["Telegram"] = str(exc)
        for channel, error in results.items():
            if error:
                log.warning("Zustellung über %s fehlgeschlagen: %s", channel, error)
        return results

    def _toasts(self, alerts: list, compat: bool) -> str:
        if len(alerts) <= MAX_SINGLE_TOASTS:
            items = [(alert_heading(a), a.message) for a in alerts]
        else:
            headings = [alert_heading(a) for a in alerts]
            summary = " · ".join(headings[:4]) + (f" · und {len(headings) - 4} weitere" if len(headings) > 4 else "")
            items = [(f"{len(alerts)} Krypto-Alarme", summary)]
        errors = [error for error in (self._toast(title, message, compat) for title, message in items) if error]
        return errors[0] if errors else ""

    def _toast(self, title: str, message: str, compat: bool) -> str:
        app_ids = [POWERSHELL_APP_ID] if compat else list(dict.fromkeys([self._app_id(), POWERSHELL_APP_ID]))
        error = ""
        for app_id in app_ids:
            ok, error = show_toast(title, message, app_id)
            if ok:
                return ""
            log.info("Windows-Meldung mit Absender %s fehlgeschlagen: %s", app_id, error)
        return error or "unbekannter Fehler"

    def _app_id(self) -> str:
        """Beim ersten Mal als Absender anmelden; klappt das nicht, als „Windows PowerShell“ senden."""
        if self._app_id_checked is None:
            try:
                register_app_id(APP_ID, APP_NAME, self.icon_path)
                self._app_id_checked = APP_ID
            except OSError as exc:
                log.warning("Anmeldung als Absender bei Windows fehlgeschlagen: %s", exc)
                self._app_id_checked = POWERSHELL_APP_ID
        return self._app_id_checked
