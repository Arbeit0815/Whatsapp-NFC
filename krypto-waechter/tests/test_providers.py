import json
import unittest

from helpers import FakeRequest

from krypto_waechter.net import NetError, RateLimitError
from krypto_waechter.providers import (Binance, CoinGecko, DemoMarket, parse_binance_ticker, parse_coingecko_market,
                                       split_symbol)

# Aufbau wie in der echten Antwort von /api/v3/coins/markets (gekürzt)
BITCOIN = {
    "id": "bitcoin", "symbol": "btc", "name": "Bitcoin",
    "image": "https://coin-images.coingecko.com/coins/images/1/large/bitcoin.png",
    "current_price": 57123.45, "market_cap": 1127000000000, "market_cap_rank": 1,
    "total_volume": 23000000000, "high_24h": 58000, "low_24h": 56000, "price_change_24h": -812.5,
    "price_change_percentage_24h": -1.4, "last_updated": "2026-09-28T04:00:00.000Z",
    "price_change_percentage_1h_in_currency": 0.25, "price_change_percentage_24h_in_currency": -1.41,
    "price_change_percentage_7d_in_currency": 3.9,
}
NEW_COIN = {
    "id": "neuer-coin", "symbol": "neu", "name": "Neuer Coin", "current_price": 0.00001234,
    "market_cap_rank": None, "price_change_percentage_24h": 12.5,
    "price_change_percentage_1h_in_currency": None,
}


class CoinGeckoTest(unittest.TestCase):
    def test_parse_market_item(self):
        quote = parse_coingecko_market(BITCOIN, "eur", 1000.0)
        self.assertEqual(quote.key, "coingecko:bitcoin")
        self.assertEqual(quote.symbol, "BTC")
        self.assertEqual(quote.currency, "EUR")
        self.assertEqual(quote.price, 57123.45)
        self.assertEqual(quote.changes, {"1h": 0.25, "24h": -1.41, "7d": 3.9})
        self.assertEqual(quote.rank, 1)
        self.assertEqual(quote.label, "Bitcoin (BTC)")

    def test_missing_values_are_tolerated(self):
        quote = parse_coingecko_market(NEW_COIN, "usd", 0)
        self.assertEqual(quote.changes, {"1h": None, "24h": 12.5, "7d": None})
        self.assertIsNone(quote.rank)
        self.assertIsNone(parse_coingecko_market({"id": "x", "current_price": None}, "eur", 0))
        self.assertIsNone(parse_coingecko_market({"id": "x", "current_price": 0}, "eur", 0))
        self.assertIsNone(parse_coingecko_market("unsinn", "eur", 0))

    def test_quotes_request(self):
        request = FakeRequest([BITCOIN])
        quotes = CoinGecko("eur", request=request).quotes(["bitcoin", "ethereum", "bitcoin"])
        self.assertEqual(list(quotes), ["coingecko:bitcoin"])
        call = request.calls[0]
        self.assertEqual(call["url"], "https://api.coingecko.com/api/v3/coins/markets")
        self.assertEqual(call["params"]["ids"], "bitcoin,ethereum")
        self.assertEqual(call["params"]["vs_currency"], "eur")
        self.assertEqual(call["params"]["per_page"], 2)
        self.assertEqual(call["params"]["price_change_percentage"], "1h,24h,7d")
        self.assertIsNone(call["headers"])

    def test_api_key_header_and_top_list(self):
        request = FakeRequest([BITCOIN, NEW_COIN])
        quotes = CoinGecko("usd", api_key=" CG-abc ", request=request).top(500)
        self.assertEqual(len(quotes), 2)
        call = request.calls[0]
        self.assertEqual(call["headers"], {"x-cg-demo-api-key": "CG-abc"})
        self.assertEqual(call["params"]["per_page"], 250)
        self.assertNotIn("ids", call["params"])

    def test_many_ids_are_split(self):
        request = FakeRequest([])
        CoinGecko(request=request).quotes([f"coin-{i}" for i in range(300)])
        self.assertEqual([c["params"]["per_page"] for c in request.calls], [250, 50])

    def test_search(self):
        request = FakeRequest({"coins": [
            {"id": "solana", "name": "Solana", "api_symbol": "solana", "symbol": "SOL", "market_cap_rank": 5},
            {"id": "solana-name-service", "name": "Solana Name Service", "symbol": "SNS", "market_cap_rank": None},
            {"name": "kaputt"},
        ], "exchanges": [], "categories": []})
        results = CoinGecko(request=request).search("sol")
        self.assertEqual([r["id"] for r in results], ["solana", "solana-name-service"])
        self.assertEqual(results[0]["info"], "Rang 5")
        self.assertEqual(request.calls[0]["params"], {"query": "sol"})

    def test_errors(self):
        with self.assertRaises(RateLimitError) as caught:
            CoinGecko(request=FakeRequest(RateLimitError("Zu viele Anfragen (HTTP 429)", status=429))).top(10)
        self.assertIn("CoinGecko", str(caught.exception))
        with self.assertRaises(NetError) as caught:
            CoinGecko(api_key="x", request=FakeRequest(NetError("HTTP 401", status=401))).top(10)
        self.assertIn("API-Key", str(caught.exception))
        with self.assertRaises(NetError):
            CoinGecko(request=FakeRequest({"status": "komisch"})).top(10)


def ticker_24h(symbol, price, pct):
    return {"symbol": symbol, "priceChange": "1.0", "priceChangePercent": str(pct), "weightedAvgPrice": "1",
            "prevClosePrice": "1", "lastPrice": str(price), "openPrice": "1", "highPrice": "1", "lowPrice": "1",
            "volume": "1", "quoteVolume": "1", "openTime": 0, "closeTime": 1, "firstId": 1, "lastId": 2, "count": 2}


def rolling(symbol, open_price, last_price):
    return {"symbol": symbol, "openPrice": str(open_price), "lastPrice": str(last_price), "highPrice": "1",
            "lowPrice": "1", "volume": "1", "quoteVolume": "1", "openTime": 0, "closeTime": 1}


class FakeBinance:
    """Simuliert die Binance-API inklusive Fehler bei unbekannten Symbolen."""

    def __init__(self, prices, down_hosts=()):
        self.prices = prices
        self.down_hosts = down_hosts
        self.calls = []

    def __call__(self, url, params=None, headers=None, data=None, timeout=20.0):
        self.calls.append((url, params))
        if any(url.startswith(host) for host in self.down_hosts):
            raise NetError("Keine Verbindung (Name or service not known)")
        if url.endswith("/api/v3/ticker/price"):
            return [{"symbol": s, "price": str(p)} for s, p in self.prices.items()] + [{"symbol": "OLDEUR", "price": "0"}]
        symbols = json.loads(params["symbols"])
        if any(s not in self.prices for s in symbols):
            raise NetError("HTTP 400: Invalid symbol.", status=400, code=-1121)
        if url.endswith("/api/v3/ticker/24hr"):
            return [ticker_24h(s, self.prices[s], -2.5) for s in symbols]
        factor = {"1h": 0.98, "7d": 0.8}[params["windowSize"]]
        return [rolling(s, self.prices[s] * factor, self.prices[s]) for s in symbols]


class BinanceTest(unittest.TestCase):
    def test_split_symbol(self):
        self.assertEqual(split_symbol("BTCEUR"), ("BTC", "EUR"))
        self.assertEqual(split_symbol("ETHUSDT"), ("ETH", "USDT"))
        self.assertEqual(split_symbol("AUDIOUSDT"), ("AUDIO", "USDT"))
        self.assertEqual(split_symbol("ETHBTC"), ("ETH", "BTC"))
        self.assertEqual(split_symbol("USDT"), ("USDT", ""))

    def test_parse_ticker(self):
        quote = parse_binance_ticker(ticker_24h("SOLEUR", 123.45, 4.2), 5.0)
        self.assertEqual(quote.key, "binance:SOLEUR")
        self.assertEqual((quote.symbol, quote.currency, quote.price), ("SOL", "EUR", 123.45))
        self.assertEqual(quote.changes["24h"], 4.2)
        self.assertEqual(quote.label, "SOL/EUR (Binance)")
        self.assertIsNone(parse_binance_ticker(ticker_24h("SOLEUR", 0, 0), 0))

    def test_quotes_with_rolling_windows(self):
        api = FakeBinance({"BTCEUR": 50000.0, "ETHUSDT": 2500.0})
        quotes = Binance(request=api).quotes(["btceur", "ETHUSDT"])
        self.assertEqual(set(quotes), {"binance:BTCEUR", "binance:ETHUSDT"})
        btc = quotes["binance:BTCEUR"]
        self.assertEqual(btc.changes["24h"], -2.5)
        self.assertAlmostEqual(btc.changes["1h"], (1 / 0.98 - 1) * 100)
        self.assertAlmostEqual(btc.changes["7d"], 25.0)
        first_url, first_params = api.calls[0]
        self.assertEqual(first_url, "https://api.binance.com/api/v3/ticker/24hr")
        self.assertEqual(first_params["symbols"], '["BTCEUR","ETHUSDT"]')

    def test_unknown_symbol_is_skipped_and_remembered(self):
        api = FakeBinance({"BTCEUR": 50000.0})
        binance = Binance(request=api)
        quotes = binance.quotes(["BTCEUR", "GIBTSNICHT"])
        self.assertEqual(list(quotes), ["binance:BTCEUR"])
        self.assertEqual(binance.invalid_symbols, {"GIBTSNICHT"})
        api.calls.clear()
        binance.quotes(["BTCEUR", "GIBTSNICHT"])
        self.assertEqual(json.loads(api.calls[0][1]["symbols"]), ["BTCEUR"])

    def test_falls_back_to_second_server(self):
        api = FakeBinance({"BTCEUR": 50000.0}, down_hosts=("https://api.binance.com",))
        binance = Binance(request=api)
        self.assertIn("binance:BTCEUR", binance.quotes(["BTCEUR"]))
        self.assertTrue(api.calls[-1][0].startswith("https://data-api.binance.vision"))
        api.calls.clear()
        binance.quotes(["BTCEUR"])  # merkt sich den funktionierenden Server
        self.assertTrue(api.calls[0][0].startswith("https://data-api.binance.vision"))

    def test_all_servers_down(self):
        api = FakeBinance({}, down_hosts=("https://",))
        with self.assertRaises(NetError) as caught:
            Binance(request=api).quotes(["BTCEUR"])
        self.assertIn("Binance", str(caught.exception))

    def test_rolling_window_failure_is_not_fatal(self):
        def api(url, params=None, headers=None, data=None, timeout=20.0):
            if url.endswith("/ticker/24hr"):
                return [ticker_24h("BTCEUR", 100, 1)]
            raise NetError("HTTP 500", status=500)

        quote = Binance(request=api).quotes(["BTCEUR"])["binance:BTCEUR"]
        self.assertIsNone(quote.changes["1h"])
        self.assertEqual(quote.changes["24h"], 1.0)

    def test_search_prefers_euro_pairs(self):
        api = FakeBinance({"BTCUSDT": 60000, "BTCEUR": 55000, "WBTCBTC": 1, "SOLEUR": 120})
        results = Binance(request=api).search("btc")
        self.assertEqual([r["id"] for r in results], ["BTCEUR", "BTCUSDT", "WBTCBTC"])
        self.assertEqual(results[0]["name"], "BTC/EUR")
        self.assertEqual(results[0]["info"], "55.000,00 €")


class DemoTest(unittest.TestCase):
    def test_demo_produces_quotes_and_jumps(self):
        demo = DemoMarket(seed=1)
        coins = [{"source": "coingecko", "id": "bitcoin"}, {"source": "coingecko", "id": "ethereum"},
                 {"source": "binance", "id": "SOLEUR"}]
        prices = []
        for _ in range(4):
            demo.tick(coins)
            quotes = demo.quotes(coins, "eur")
            prices.append(quotes["coingecko:ethereum"].price)
        self.assertEqual(quotes["binance:SOLEUR"].currency, "EUR")
        self.assertEqual(quotes["binance:SOLEUR"].label, "SOL/EUR (Binance)")
        self.assertGreater(prices[2] / prices[1], 1.05)  # eingeplanter Sprung beim 3. Abruf
        self.assertEqual(len(demo.top(10, "eur")), 10)
        self.assertEqual(demo.search("coingecko", "sol")[0]["id"], "solana")
        self.assertEqual(demo.search("binance", "btc")[0]["id"], "BTCEUR")


if __name__ == "__main__":
    unittest.main()
