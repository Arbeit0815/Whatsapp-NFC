"""HTTP-Zugriffe (nur Python-Standardbibliothek, keine Zusatzpakete nötig)."""

from __future__ import annotations

import json
import socket
import urllib.error
import urllib.parse
import urllib.request

from . import __version__

USER_AGENT = f"Krypto-Waechter/{__version__}"


class NetError(Exception):
    """Fehler beim Abruf; die Meldung ist für Anwender gedacht."""

    def __init__(self, message: str, status: int | None = None, code=None):
        super().__init__(message)
        self.status = status  # HTTP-Status, falls vorhanden
        self.code = code  # Fehlercode der API, falls vorhanden


class RateLimitError(NetError):
    """Der Server meldet zu viele Anfragen (HTTP 429)."""


def _error_details(exc: urllib.error.HTTPError) -> tuple[str, object]:
    try:
        body = exc.read(4000)
        data = json.loads(body.decode("utf-8", "replace"))
    except Exception:
        return "", None
    if not isinstance(data, dict):
        return "", None
    status = data.get("status") if isinstance(data.get("status"), dict) else {}
    message = data.get("msg") or data.get("description") or data.get("error") or status.get("error_message") or ""
    code = data.get("code", data.get("error_code", status.get("error_code")))
    return str(message)[:200], code


def request_json(url: str, params: dict | None = None, headers: dict | None = None,
                 data: dict | None = None, timeout: float = 20.0):
    """Ruft eine URL ab (GET bzw. POST mit JSON) und liefert die JSON-Antwort."""
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    all_headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    body = None
    if data is not None:
        body = json.dumps(data).encode("utf-8")
        all_headers["Content-Type"] = "application/json"
    all_headers.update(headers or {})
    request = urllib.request.Request(url, data=body, headers=all_headers, method="POST" if body else "GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        detail, code = _error_details(exc)
        if exc.code in (418, 429):
            raise RateLimitError(f"Zu viele Anfragen (HTTP {exc.code})", status=exc.code, code=code) from None
        message = f"HTTP {exc.code}" + (f": {detail}" if detail else "")
        raise NetError(message, status=exc.code, code=code) from None
    except urllib.error.URLError as exc:
        raise NetError(f"Keine Verbindung ({exc.reason})") from None
    except (socket.timeout, TimeoutError):
        raise NetError("Zeitüberschreitung – der Server antwortet nicht") from None
    except OSError as exc:
        raise NetError(f"Netzwerkfehler ({exc})") from None
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise NetError("Ungültige Antwort vom Server") from None
