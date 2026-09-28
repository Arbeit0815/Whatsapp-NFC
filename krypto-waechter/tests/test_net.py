"""Echte HTTP-Abrufe gegen einen lokalen Testserver (ohne Internet)."""

import json
import threading
import time
import unittest
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import helpers  # noqa: F401  (setzt den Suchpfad)
from test_providers import BITCOIN, ticker_24h

from krypto_waechter import __version__
from krypto_waechter.net import NetError, RateLimitError, request_json
from krypto_waechter.providers import Binance, CoinGecko


class Handler(BaseHTTPRequestHandler):
    received = []

    def _send(self, status, body, content_type="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path, _, query = self.path.partition("?")
        params = dict(urllib.parse.parse_qsl(query))
        Handler.received.append((path, params, dict(self.headers)))
        if path == "/ok":
            self._send(200, {"params": params, "agent": self.headers.get("User-Agent"),
                             "key": self.headers.get("x-test")})
        elif path == "/binance-error":
            self._send(400, {"code": -1121, "msg": "Invalid symbol."})
        elif path == "/limit":
            self._send(429, {"status": {"error_code": 429, "error_message": "You've exceeded the Rate Limit."}})
        elif path == "/html":
            self._send(200, b"<html>kaputt</html>", "text/html")
        elif path == "/slow":
            time.sleep(1.5)
            self._send(200, {})
        elif path == "/api/v3/coins/markets":
            self._send(200, [BITCOIN])
        elif path == "/api/v3/ticker/24hr":
            self._send(200, [ticker_24h(s, 50000, 1.5) for s in json.loads(params["symbols"])])
        elif path == "/api/v3/ticker":
            self._send(200, [{"symbol": s, "openPrice": "100", "lastPrice": "103"} for s in json.loads(params["symbols"])])
        else:
            self._send(404, {"error": "unbekannt"})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self._send(200, {"echo": body, "type": self.headers.get("Content-Type")})

    def log_message(self, *args):
        pass


class NetTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        Handler.received.clear()

    def test_get_with_params_and_headers(self):
        data = request_json(self.base + "/ok", params={"ids": "bitcoin,ethereum", "x": "ä"}, headers={"x-test": "42"})
        self.assertEqual(data["params"], {"ids": "bitcoin,ethereum", "x": "ä"})
        self.assertEqual(data["agent"], f"Krypto-Waechter/{__version__}")
        self.assertEqual(data["key"], "42")

    def test_post_json(self):
        data = request_json(self.base + "/post", data={"chat_id": "1", "text": "Grüße 📈"})
        self.assertEqual(data["echo"], {"chat_id": "1", "text": "Grüße 📈"})
        self.assertEqual(data["type"], "application/json")

    def test_api_error_details(self):
        with self.assertRaises(NetError) as caught:
            request_json(self.base + "/binance-error")
        self.assertEqual((caught.exception.status, caught.exception.code), (400, -1121))
        self.assertEqual(str(caught.exception), "HTTP 400: Invalid symbol.")

    def test_rate_limit(self):
        with self.assertRaises(RateLimitError) as caught:
            request_json(self.base + "/limit")
        self.assertEqual(caught.exception.status, 429)

    def test_not_json(self):
        with self.assertRaises(NetError) as caught:
            request_json(self.base + "/html")
        self.assertIn("Ungültige Antwort", str(caught.exception))

    def test_timeout(self):
        with self.assertRaises(NetError):
            request_json(self.base + "/slow", timeout=0.3)

    def test_connection_refused(self):
        free = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        port = free.server_address[1]
        free.server_close()
        with self.assertRaises(NetError) as caught:
            request_json(f"http://127.0.0.1:{port}/ok", timeout=3)
        self.assertIn("Keine Verbindung", str(caught.exception))

    def test_coingecko_over_http(self):
        gecko = CoinGecko("eur", api_key="CG-test")
        gecko.BASE_URL = self.base + "/api/v3"
        quotes = gecko.quotes(["bitcoin"])
        self.assertEqual(quotes["coingecko:bitcoin"].price, 57123.45)
        path, params, headers = Handler.received[0]
        self.assertEqual(params["price_change_percentage"], "1h,24h,7d")
        self.assertEqual(params["ids"], "bitcoin")
        self.assertEqual({k.lower(): v for k, v in headers.items()}["x-cg-demo-api-key"], "CG-test")

    def test_binance_over_http(self):
        binance = Binance()
        binance.BASE_URLS = (self.base,)
        quote = binance.quotes(["BTCEUR", "ETHEUR"])["binance:ETHEUR"]
        self.assertEqual(quote.price, 50000)
        self.assertAlmostEqual(quote.changes["1h"], 3.0)
        self.assertEqual(json.loads(Handler.received[0][1]["symbols"]), ["BTCEUR", "ETHEUR"])
        self.assertEqual(Handler.received[1][1]["windowSize"], "1h")


if __name__ == "__main__":
    unittest.main()
