import unittest

from helpers import make_config, quote

from krypto_waechter.engine import AlertLatch, Engine, PriceHistory, display_windows

T0 = 1_700_000_000.0
MINUTE = 60.0


def coin(coin_id="bitcoin", **extra):
    entry = {"source": "coingecko", "id": coin_id, "name": coin_id.title(), "symbol": coin_id[:3].upper()}
    entry.update(extra)
    return entry


def rules(*pairs):
    return [{"minutes": m, "percent": p, "enabled": True} for m, p in pairs]


class PriceHistoryTest(unittest.TestCase):
    def test_change_over_window(self):
        history = PriceHistory()
        for i in range(16):
            history.add("k", T0 + i * MINUTE, 100 + i)
        self.assertAlmostEqual(history.change_percent("k", 15, tolerance=90), 15.0)
        self.assertAlmostEqual(history.change_percent("k", 5, tolerance=90), (115 / 110 - 1) * 100)

    def test_too_little_history(self):
        history = PriceHistory()
        self.assertIsNone(history.change_percent("k", 15, 90))
        history.add("k", T0, 100)
        history.add("k", T0 + 5 * MINUTE, 110)
        self.assertIsNone(history.change_percent("k", 15, 90))
        # knapp zu kurz, aber innerhalb der Toleranz
        self.assertAlmostEqual(history.change_percent("k", 6, 90), 10.0)

    def test_gap_after_standby_gives_no_value(self):
        history = PriceHistory()
        for i in range(10):
            history.add("k", T0 + i * MINUTE, 100)
        history.add("k", T0 + 8 * 3600, 150)  # PC war acht Stunden im Standby
        self.assertIsNone(history.change_percent("k", 15, 135))

    def test_old_samples_are_removed_and_duplicates_ignored(self):
        history = PriceHistory(max_age=3600)
        history.add("k", T0, 1)
        history.add("k", T0 + 1800, 2)
        history.add("k", T0 + 1800, 3)  # gleicher Zeitpunkt ersetzt den Wert
        history.add("k", T0 + 1000, 9)  # älter – wird ignoriert
        history.add("k", T0 + 4000, 4)
        self.assertEqual(history.samples("k"), 2)
        self.assertAlmostEqual(history.change_percent("k", 37, 300), (4 / 3 - 1) * 100)

    def test_retain_and_clear(self):
        history = PriceHistory()
        for key in ("coingecko:a", "coingecko:b", "binance:C"):
            history.add(key, T0, 1)
        history.retain({"coingecko:a", "binance:C"})
        history.clear("coingecko:")
        self.assertEqual(history.samples("coingecko:a"), 0)
        self.assertEqual(history.samples("coingecko:b"), 0)
        self.assertEqual(history.samples("binance:C"), 1)


class LatchTest(unittest.TestCase):
    def test_fire_escalate_rearm(self):
        latch = AlertLatch()
        check = lambda now, value: latch.check("id", now, active=value >= 5, level=value, step=5,  # noqa: E731
                                               rearm=value < 2.5, cooldown=1800)
        self.assertTrue(check(T0, 5.5))
        self.assertFalse(check(T0 + 60, 7))
        self.assertTrue(check(T0 + 120, 10.6))  # weitere Stufe -> sofort
        self.assertFalse(check(T0 + 180, 1))  # ruhig, aber Mindestpause noch nicht vorbei
        self.assertFalse(check(T0 + 240, 6))
        self.assertFalse(check(T0 + 2000, 1))  # jetzt wieder scharf
        self.assertTrue(check(T0 + 2060, 5.1))

    def test_prune(self):
        latch = AlertLatch()
        latch.check("a", T0, active=True, rearm=False, cooldown=0)
        latch.prune(T0 + 10, max_age=5)
        self.assertTrue(latch.check("a", T0 + 20, active=True, rearm=False, cooldown=0))


class EngineTest(unittest.TestCase):
    def setUp(self):
        self.engine = Engine()

    def run_engine(self, cfg, quotes, now, scanner=()):
        fresh = {q.key: q for q in quotes}
        return self.engine.evaluate(cfg, fresh, fresh, list(scanner), now)

    def test_display_windows(self):
        cfg = make_config(move_rules=rules((15, 3), (60, 5)) + [{"minutes": 5, "percent": 2, "enabled": False}])
        self.assertEqual(display_windows(cfg), [15, 60, 1440, 10080])

    def test_api_values_are_used_when_available(self):
        cfg = make_config(coins=[coin()], move_rules=rules((60, 5)))
        result = self.run_engine(cfg, [quote(price=100, h1=1.5, h24=-2, d7=10)], T0)
        self.assertEqual(result.changes["coingecko:bitcoin"], {60: 1.5, 1440: -2, 10080: 10})

    def test_short_term_move_alert(self):
        cfg = make_config(coins=[coin()], move_rules=rules((15, 3)), scanner={"enabled": False})
        alerts = []
        for i, price in enumerate([100] * 15 + [104]):
            alerts += self.run_engine(cfg, [quote(price=price)], T0 + i * MINUTE).alerts
        self.assertEqual(len(alerts), 1)
        alert = alerts[0]
        self.assertEqual((alert.kind, alert.direction, alert.icon), ("move", "up", "📈"))
        self.assertEqual(alert.title, "Bitcoin (BIT) steigt: +4,00 % in 15 Min")
        self.assertIn("Kurs: 104,00 €", alert.message)
        # bleibt hoch -> keine Wiederholung
        more = self.run_engine(cfg, [quote(price=104.5)], T0 + 16 * MINUTE).alerts
        self.assertEqual(more, [])

    def test_crash_escalates_and_is_grouped(self):
        cfg = make_config(coins=[coin()], move_rules=rules((15, 3), (60, 5)), scanner={"enabled": False})
        first = self.run_engine(cfg, [quote(price=100, h1=-3.5)], T0).alerts
        self.assertEqual(first, [])
        second = self.run_engine(cfg, [quote(price=94, h1=-6)], T0 + 60).alerts
        self.assertEqual(len(second), 1)
        self.assertEqual(second[0].direction, "down")
        self.assertIn("fällt: -6,00 % in 1 Std", second[0].title)
        third = self.run_engine(cfg, [quote(price=88, h1=-11.2)], T0 + 120).alerts
        self.assertEqual(len(third), 1)  # neue Stufe (-5 % weiter) -> erneut melden
        self.assertIn("-11,20 %", third[0].title)

    def test_multiple_windows_in_one_alert(self):
        cfg = make_config(coins=[coin()], move_rules=rules((60, 5), (1440, 10)), scanner={"enabled": False})
        alerts = self.run_engine(cfg, [quote(price=80, h1=-8, h24=-25)], T0).alerts
        self.assertEqual(len(alerts), 1)
        self.assertIn("-25,00 % in 24 Std", alerts[0].title)  # stärkste Bewegung im Titel
        self.assertIn("-8,00 % in 1 Std · -25,00 % in 24 Std", alerts[0].message)

    def test_active_marks_rows(self):
        cfg = make_config(coins=[coin(), coin("ethereum")], move_rules=rules((60, 5)), scanner={"enabled": False})
        result = self.run_engine(cfg, [quote(price=1, h1=7), quote("ethereum", price=1, h1=1)], T0)
        self.assertEqual(result.active, {"coingecko:bitcoin": "up"})

    def test_price_targets(self):
        cfg = make_config(coins=[coin(alarm_above=110, alarm_below=90)], move_rules=[], scanner={"enabled": False},
                          cooldown_minutes=0)
        prices = [100, 111, 112, 109.5, 108, 111, 89]
        titles = []
        for i, price in enumerate(prices):
            titles += [a.title for a in self.run_engine(cfg, [quote(price=price)], T0 + i * MINUTE).alerts]
        self.assertEqual(titles, ["Bitcoin (BIT) über 110,00 €", "Bitcoin (BIT) über 110,00 €",
                                  "Bitcoin (BIT) unter 90,00 €"])

    def test_changed_price_target_is_armed_again(self):
        cfg = make_config(coins=[coin(alarm_above=110)], move_rules=[], scanner={"enabled": False})
        self.assertEqual(len(self.run_engine(cfg, [quote(price=111)], T0).alerts), 1)
        cfg["coins"][0]["alarm_above"] = 115
        self.assertEqual(self.run_engine(cfg, [quote(price=112)], T0 + 60).alerts, [])
        self.assertEqual(len(self.run_engine(cfg, [quote(price=116)], T0 + 120).alerts), 1)

    def test_portfolio_values(self):
        cfg = make_config(coins=[coin(amount=0.5, buy_price=40000), coin("ethereum", amount=2),
                                 coin("solana", amount=1, buy_price=100)], move_rules=[], scanner={"enabled": False},
                          portfolio={"profit_percent": 0, "loss_percent": 0, "move_percent": 0, "move_minutes": 60})
        result = self.run_engine(cfg, [quote(price=50000, h24=25), quote("ethereum", price=2000, h24=0)], T0)
        btc, eth, sol = result.positions
        self.assertEqual((btc.value, btc.cost, btc.pl, btc.pl_pct), (25000, 20000, 5000, 25))
        self.assertIsNone(eth.pl)
        self.assertIsNone(sol.price)  # noch kein Kurs
        total = result.totals[0]
        self.assertEqual((total.currency, total.value, total.cost, total.pl), ("EUR", 29000, 20000, 5000))
        self.assertAlmostEqual(total.pl_pct, 25)
        # 24 Std: BTC +25 % (vorher 20.000 €), ETH unverändert (4.000 €) -> 24.000 € -> 29.000 €
        self.assertAlmostEqual(total.change_24h, (29000 / 24000 - 1) * 100)
        self.assertAlmostEqual(total.change_24h_abs, 5000)

    def test_profit_and_loss_alerts(self):
        cfg = make_config(coins=[coin(amount=1, buy_price=100)], move_rules=[], scanner={"enabled": False},
                          portfolio={"profit_percent": 25, "loss_percent": 15, "move_percent": 0, "move_minutes": 60})
        kinds = []
        # Wiederholung erst, wenn es eine ganze Stufe (25 % bzw. 15 %) weitergeht
        for i, price in enumerate([110, 126, 130, 151, 120, 84, 70, 68]):
            kinds += [(a.kind, a.title) for a in self.run_engine(cfg, [quote(price=price)], T0 + i * MINUTE).alerts]
        self.assertEqual(kinds, [
            ("profit", "Bitcoin (BIT): Gewinn +26,00 % seit Kauf"),
            ("profit", "Bitcoin (BIT): Gewinn +51,00 % seit Kauf"),
            ("loss", "Bitcoin (BIT): Verlust -16,00 % seit Kauf"),
            ("loss", "Bitcoin (BIT): Verlust -32,00 % seit Kauf"),
        ])

    def test_portfolio_move_alert(self):
        cfg = make_config(coins=[coin(amount=1), coin("ethereum", amount=10)], move_rules=[],
                          scanner={"enabled": False},
                          portfolio={"profit_percent": 0, "loss_percent": 0, "move_percent": 5, "move_minutes": 60})
        calm = self.run_engine(cfg, [quote(price=1000, h1=1), quote("ethereum", price=100, h1=-1)], T0).alerts
        self.assertEqual(calm, [])
        alerts = self.run_engine(cfg, [quote(price=900, h1=-10), quote("ethereum", price=90, h1=-10)], T0 + 60).alerts
        self.assertEqual(len(alerts), 1)
        self.assertEqual((alerts[0].kind, alerts[0].direction), ("portfolio", "down"))
        self.assertEqual(alerts[0].title, "Portfolio -10,00 % in 1 Std")
        self.assertIn("Veränderung: -200,00 €", alerts[0].message)

    def test_scanner_alerts_skip_watchlist(self):
        cfg = make_config(coins=[coin()], move_rules=[],
                          scanner={"enabled": True, "top_n": 100, "percent_1h": 10, "percent_24h": 25})
        scanner = [quote(price=1, h1=30, rank=1), quote("pepe", price=0.00001, h1=12, h24=5, rank=30, name="Pepe",
                                                         symbol="PEPE"), quote("dogecoin", price=0.1, h24=-3)]
        alerts = self.run_engine(cfg, [quote(price=1)], T0, scanner=scanner).alerts
        self.assertEqual([a.title for a in alerts], ["Markt: Pepe +12,00 % in 1 Std"])
        self.assertIn("Rang 30", alerts[0].message)
        again = self.run_engine(cfg, [quote(price=1)], T0 + 60, scanner=scanner).alerts
        self.assertEqual(again, [])

    def test_stale_quotes_do_not_alert(self):
        cfg = make_config(coins=[coin(alarm_above=10)], move_rules=[], scanner={"enabled": False})
        known = {"coingecko:bitcoin": quote(price=50)}
        result = self.engine.evaluate(cfg, {}, known, [], T0)
        self.assertEqual(result.alerts, [])
        self.assertIn("coingecko:bitcoin", result.changes)

    def test_reset_forgets_currency(self):
        cfg = make_config(coins=[coin(alarm_above=10)], move_rules=[], scanner={"enabled": False})
        self.assertEqual(len(self.run_engine(cfg, [quote(price=50)], T0).alerts), 1)
        self.engine.reset("coingecko:")
        self.assertEqual(len(self.run_engine(cfg, [quote(price=50)], T0 + 1).alerts), 1)


if __name__ == "__main__":
    unittest.main()
