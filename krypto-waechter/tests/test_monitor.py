import csv
import tempfile
import threading
import unittest
from pathlib import Path

from helpers import quote

from krypto_waechter.config import ConfigStore
from krypto_waechter.monitor import Monitor
from krypto_waechter.net import NetError, RateLimitError


class RecordingNotifier:
    def __init__(self):
        self.sent = []

    def send(self, alerts):
        self.sent.extend(alerts)


class FakeGecko:
    """Ersatz für CoinGecko: Top-Liste und Einzelabfrage."""

    instances = []

    def __init__(self, currency, api_key):
        self.currency, self.api_key = currency, api_key
        self.top_calls, self.quote_calls = [], []
        self.error = None
        self.prices = {"bitcoin": 50000.0, "ethereum": 2500.0, "solana": 120.0, "kleiner-coin": 0.5}
        self.top_ids = ["bitcoin", "ethereum", "solana"]
        FakeGecko.instances.append(self)

    def _quote(self, coin_id, rank=None):
        return quote(coin_id, price=self.prices[coin_id], currency=self.currency.upper(), h1=0.5, h24=1.0, rank=rank)

    def top(self, count):
        self.top_calls.append(count)
        if self.error:
            raise self.error
        return [self._quote(cid, rank) for rank, cid in enumerate(self.top_ids, start=1)]

    def quotes(self, ids):
        self.quote_calls.append(list(ids))
        if self.error:
            raise self.error
        return {f"coingecko:{cid}": self._quote(cid) for cid in ids if cid in self.prices}

    def search(self, query):
        return [{"source": "coingecko", "id": "bitcoin", "name": "Bitcoin", "symbol": "BTC", "info": ""}]


class FakeBinance:
    def __init__(self):
        self.invalid_symbols = set()
        self.calls = []

    def quotes(self, symbols):
        self.calls.append(list(symbols))
        self.invalid_symbols.update(s for s in symbols if s == "FALSCH")
        return {f"binance:{s}": quote(s, price=10.0, source="binance", currency="EUR") for s in symbols if s != "FALSCH"}


class FakeClock:
    """Jeder Abruf ist eine Minute später (unabhängig von der Genauigkeit der Systemuhr)."""

    def __init__(self):
        self.now = 1_700_000_000.0
        self.step = 60

    def __call__(self):
        self.now += self.step
        return self.now


class MonitorTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self.tmp.name)
        self.store = ConfigStore(self.folder / "config.json")
        self.store.update(lambda cfg: cfg.update(coins=[
            {"source": "coingecko", "id": "bitcoin"}, {"source": "coingecko", "id": "kleiner-coin"},
            {"source": "coingecko", "id": "gibtsnicht"}, {"source": "binance", "id": "BTCEUR"},
            {"source": "binance", "id": "FALSCH"},
        ]))
        self.notifier = RecordingNotifier()
        self.snapshots = []
        FakeGecko.instances.clear()
        self.binance = FakeBinance()
        self.monitor = Monitor(self.store, self.notifier, self.snapshots.append, coingecko_factory=FakeGecko,
                               binance=self.binance, alert_log=self.folder / "alarme.csv", clock=FakeClock())

    def tearDown(self):
        self.monitor.stop()
        self.tmp.cleanup()

    def test_cycle_uses_top_list_and_fetches_the_rest(self):
        snapshot = self.monitor.run_cycle()
        gecko = FakeGecko.instances[-1]
        self.assertEqual(gecko.top_calls, [100])
        self.assertEqual(gecko.quote_calls, [["kleiner-coin", "gibtsnicht"]])
        self.assertEqual(self.binance.calls, [["BTCEUR", "FALSCH"]])
        self.assertEqual(set(snapshot.quotes), {"coingecko:bitcoin", "coingecko:kleiner-coin", "binance:BTCEUR"})
        self.assertEqual(snapshot.fresh, set(snapshot.quotes))
        self.assertEqual(len(snapshot.scanner), 3)
        self.assertEqual(snapshot.errors, ["CoinGecko kennt „gibtsnicht“ nicht",
                                           "Binance kennt das Handelspaar „FALSCH“ nicht"])
        self.assertIs(self.snapshots[-1], snapshot)

    def test_scanner_off_means_no_top_list(self):
        self.store.update(lambda cfg: cfg["scanner"].update(enabled=False))
        snapshot = self.monitor.run_cycle()
        gecko = FakeGecko.instances[-1]
        self.assertEqual(gecko.top_calls, [])
        self.assertEqual(gecko.quote_calls, [["bitcoin", "kleiner-coin", "gibtsnicht"]])
        self.assertEqual(snapshot.scanner, [])

    def test_rate_limit_pauses_the_source(self):
        self.monitor.run_cycle()
        FakeGecko.instances[-1].error = RateLimitError("CoinGecko: Zu viele Anfragen (HTTP 429)", status=429)
        snapshot = self.monitor.run_cycle()
        self.assertTrue(any("nächster Versuch in 60 s" in e and "API-Key" in e for e in snapshot.errors))
        # Die alten Kurse bleiben sichtbar, gelten aber nicht als frisch
        self.assertIn("coingecko:bitcoin", snapshot.quotes)
        self.assertNotIn("coingecko:bitcoin", snapshot.fresh)
        calls = len(FakeGecko.instances[-1].top_calls)
        self.monitor.clock.step = 10  # die Pause dauert 60 s
        snapshot = self.monitor.run_cycle()
        self.assertEqual(len(FakeGecko.instances[-1].top_calls), calls)  # während der Pause nicht gefragt
        self.assertTrue(any("Pause wegen Anfrage-Limit" in e for e in snapshot.errors))

    def test_network_error_is_reported(self):
        self.monitor.run_cycle()
        FakeGecko.instances[-1].error = NetError("CoinGecko: Keine Verbindung (timed out)")
        snapshot = self.monitor.run_cycle()
        self.assertIn("CoinGecko: Keine Verbindung (timed out)", snapshot.errors)
        FakeGecko.instances[-1].error = None
        self.assertNotIn("CoinGecko: Keine Verbindung (timed out)", self.monitor.run_cycle().errors)

    def test_currency_change_resets_history(self):
        self.monitor.run_cycle()
        self.assertGreater(self.monitor.engine.history.samples("coingecko:bitcoin"), 0)
        self.store.update(lambda cfg: cfg.update(currency="usd"))
        snapshot = self.monitor.run_cycle()
        self.assertEqual(FakeGecko.instances[-1].currency, "usd")
        self.assertEqual(self.monitor.engine.history.samples("coingecko:bitcoin"), 1)
        self.assertEqual(snapshot.quotes["coingecko:bitcoin"].currency, "USD")
        self.assertEqual(self.monitor.engine.history.samples("binance:BTCEUR"), 2)

    def test_removed_coins_disappear(self):
        self.monitor.run_cycle()
        self.store.update(lambda cfg: cfg.update(coins=cfg["coins"][:1]))
        snapshot = self.monitor.run_cycle(fetch=False)
        self.assertFalse(snapshot.fetched)
        self.assertEqual(list(snapshot.quotes), ["coingecko:bitcoin"])
        self.assertEqual(len(FakeGecko.instances[-1].top_calls), 1)  # ohne Abruf neu berechnet

    def test_alerts_are_sent_and_logged(self):
        self.store.update(lambda cfg: cfg["coins"][0].update(alarm_above=40000))
        snapshot = self.monitor.run_cycle()
        self.assertEqual([a.kind for a in snapshot.alerts], ["target"])
        self.assertEqual(self.notifier.sent, snapshot.alerts)
        with open(self.folder / "alarme.csv", encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.reader(handle, delimiter=";"))
        self.assertEqual(rows[0], ["Zeit", "Art", "Richtung", "Meldung", "Details"])
        self.assertEqual(rows[1][1:4], ["Kursalarm", "steigt", "Bitcoin (BIT) über 40.000,00 €"])

    def test_search_uses_the_right_source(self):
        self.assertEqual(self.monitor.search("coingecko", "bit")[0]["id"], "bitcoin")


class DemoMonitorTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = ConfigStore(Path(self.tmp.name) / "config.json")

    def tearDown(self):
        self.tmp.cleanup()

    def test_demo_shows_alerts_quickly(self):
        notifier = RecordingNotifier()
        monitor = Monitor(self.store, notifier, lambda snapshot: None, demo=True)
        kinds = set()
        for _ in range(10):
            kinds.update(a.kind for a in monitor.run_cycle().alerts)
        self.assertIn("move", kinds)
        self.assertIn("scanner", kinds)
        self.assertTrue(notifier.sent)

    def test_background_thread_runs_and_stops(self):
        seen = threading.Event()
        snapshots = []

        def on_update(snapshot):
            snapshots.append(snapshot)
            if len([s for s in snapshots if s.fetched]) >= 2:
                seen.set()

        monitor = Monitor(self.store, RecordingNotifier(), on_update, demo=True, interval=1)
        monitor.start()
        try:
            self.assertTrue(seen.wait(10))
            monitor.set_paused(True)
            self.assertTrue(monitor.paused)
        finally:
            monitor.stop()
        monitor._thread.join(5)
        self.assertFalse(monitor._thread.is_alive())


if __name__ == "__main__":
    unittest.main()
