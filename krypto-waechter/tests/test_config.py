import json
import tempfile
import unittest
from pathlib import Path

from helpers import make_config

from krypto_waechter.config import DEFAULT_CONFIG, ConfigStore, coin_key, coin_label, normalize_config


class NormalizeTest(unittest.TestCase):
    def test_defaults_are_complete(self):
        cfg = normalize_config({})
        self.assertEqual(cfg["currency"], "eur")
        self.assertEqual(len(cfg["coins"]), len(DEFAULT_CONFIG["coins"]))
        self.assertEqual(cfg["coins"][0]["amount"], 0.0)
        self.assertIsNone(cfg["coins"][0]["alarm_above"])

    def test_invalid_values_are_corrected(self):
        cfg = normalize_config({
            "currency": "EUR!!", "poll_interval": 1, "cooldown_minutes": "abc",
            "coins": [{"source": "coingecko", "id": " Bitcoin "}, {"source": "coingecko", "id": "bitcoin"},
                      {"source": "kraken", "id": "x"}, {"source": "binance", "id": "btc/eur", "amount": -5},
                      "unsinn", {"source": "binance", "id": ""}],
            "move_rules": [{"minutes": 60, "percent": -3}, {"minutes": 15, "percent": "4"}, {"minutes": 0}],
            "scanner": {"top_n": 5000, "percent_1h": True},
            "notifications": {"toast": 0, "telegram_token": 12345},
            "window_geometry": "kaputt",
        })
        self.assertEqual(cfg["currency"], "eur")
        self.assertEqual(cfg["poll_interval"], 20)
        self.assertEqual(cfg["cooldown_minutes"], 30)
        self.assertEqual([coin_key(c) for c in cfg["coins"]], ["coingecko:bitcoin", "binance:BTCEUR"])
        self.assertEqual(cfg["coins"][1]["amount"], 0.0)
        self.assertEqual([r["minutes"] for r in cfg["move_rules"]], [15, 60])
        self.assertEqual(cfg["move_rules"][1]["percent"], 0.1)
        self.assertEqual(cfg["scanner"]["top_n"], 250)
        self.assertEqual(cfg["scanner"]["percent_1h"], 10.0)
        self.assertFalse(cfg["notifications"]["toast"])
        self.assertEqual(cfg["notifications"]["telegram_token"], "12345")
        self.assertEqual(cfg["window_geometry"], "")

    def test_empty_watchlist_stays_empty(self):
        self.assertEqual(normalize_config({"coins": []})["coins"], [])

    def test_labels(self):
        self.assertEqual(coin_label({"source": "coingecko", "id": "bitcoin", "name": "Bitcoin", "symbol": "BTC"}),
                         "Bitcoin (BTC)")
        self.assertEqual(coin_label({"source": "coingecko", "id": "ripple", "name": "XRP", "symbol": "XRP"}), "XRP")
        self.assertEqual(coin_label({"source": "coingecko", "id": "pepe", "name": "", "symbol": ""}), "pepe")
        self.assertEqual(coin_label({"source": "binance", "id": "ETHUSDT"}), "ETHUSDT (Binance)")

    def test_make_config_helper(self):
        cfg = make_config(poll_interval=120)
        self.assertEqual(cfg["poll_interval"], 120)


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "sub" / "config.json"

    def tearDown(self):
        self.tmp.cleanup()

    def test_creates_file_and_saves_changes(self):
        store = ConfigStore(self.path)
        self.assertTrue(self.path.exists())
        store.update(lambda cfg: cfg["coins"].append({"source": "binance", "id": "SOLEUR", "amount": "2,5"}))
        saved = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(saved["coins"][-1]["id"], "SOLEUR")
        self.assertEqual(ConfigStore(self.path).get()["coins"][-1]["id"], "SOLEUR")

    def test_get_returns_a_copy(self):
        store = ConfigStore(self.path)
        store.get()["coins"].clear()
        self.assertTrue(store.get()["coins"])

    def test_broken_file_is_backed_up(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text("{ kaputt", encoding="utf-8")
        store = ConfigStore(self.path)
        self.assertEqual(store.get()["currency"], "eur")
        self.assertTrue((self.path.parent / "config.defekt.json").exists())

    def test_umlauts_survive(self):
        store = ConfigStore(self.path)
        store.update(lambda cfg: cfg["coins"][0].update(name="Bitcoin – Übersicht"))
        self.assertEqual(ConfigStore(self.path).get()["coins"][0]["name"], "Bitcoin – Übersicht")


if __name__ == "__main__":
    unittest.main()
