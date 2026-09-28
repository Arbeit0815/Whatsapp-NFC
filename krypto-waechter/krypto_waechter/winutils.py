"""Windows-Anbindung: Autostart, Taskleiste, nur eine Instanz, scharfe Darstellung."""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path

log = logging.getLogger(__name__)

IS_WINDOWS = sys.platform == "win32"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE = "KryptoWaechter"
LAUNCHER = Path(__file__).resolve().parent.parent / "Krypto-Waechter.pyw"

_instance_handle = None


def enable_dpi_awareness() -> None:
    """Verhindert eine unscharfe Darstellung bei Bildschirmskalierung (z. B. 150 %)."""
    if not IS_WINDOWS:
        return
    import ctypes

    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def set_process_app_id(app_id: str) -> None:
    """Eigenes Taskleisten-Symbol statt dem von Python."""
    if not IS_WINDOWS:
        return
    import ctypes

    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(app_id)
    except Exception:
        pass


def program_command() -> list[str]:
    """Befehl, der das Programm startet – als .exe oder mit Python ohne Konsolenfenster."""
    if getattr(sys, "frozen", False):  # als .exe gebaut
        return [sys.executable]
    python = Path(sys.executable)
    if python.with_name("pythonw.exe").exists():
        python = python.with_name("pythonw.exe")
    return [str(python), str(LAUNCHER)]


def launch_command(minimized: bool = True) -> str:
    """Befehl, mit dem Windows das Programm bei der Anmeldung startet."""
    command = " ".join(f'"{part}"' for part in program_command())
    return command + (" --minimiert" if minimized else "")


def set_autostart(enabled: bool) -> None:
    import winreg

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if enabled:
            winreg.SetValueEx(key, RUN_VALUE, 0, winreg.REG_SZ, launch_command())
        else:
            try:
                winreg.DeleteValue(key, RUN_VALUE)
            except FileNotFoundError:
                pass


def autostart_enabled() -> bool:
    if not IS_WINDOWS:
        return False
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.QueryValueEx(key, RUN_VALUE)
            return True
    except OSError:
        return False


def flash_window(hwnd: int) -> None:
    """Lässt das Taskleistensymbol blinken, bis das Fenster wieder aktiv ist."""
    if not IS_WINDOWS:
        return
    import ctypes
    from ctypes import wintypes

    class FLASHWINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.UINT), ("hwnd", wintypes.HWND), ("dwFlags", wintypes.DWORD),
                    ("uCount", wintypes.UINT), ("dwTimeout", wintypes.DWORD)]

    flash_all, until_foreground = 0x3, 0xC
    info = FLASHWINFO(ctypes.sizeof(FLASHWINFO), hwnd, flash_all | until_foreground, 0, 0)
    ctypes.windll.user32.FlashWindowEx(ctypes.byref(info))


def acquire_single_instance(name: str) -> bool:
    """False, wenn das Programm schon läuft (verhindert doppelte Meldungen)."""
    global _instance_handle
    if not IS_WINDOWS:
        return True
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    handle = kernel32.CreateMutexW(None, False, f"Local\\{name}")
    if not handle:
        return True
    if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
        kernel32.CloseHandle(handle)
        return False
    _instance_handle = handle  # bleibt bis zum Programmende geöffnet
    return True


def open_path(path: Path) -> None:
    """Öffnet einen Ordner oder eine Datei mit dem Standardprogramm."""
    if IS_WINDOWS:
        os.startfile(str(path))
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])
