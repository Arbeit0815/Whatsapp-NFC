"""Oberflächentests – laufen nur, wenn ein Bildschirm (bzw. Xvfb) vorhanden ist."""

import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import helpers  # noqa: F401  (setzt den Suchpfad)

from krypto_waechter.config import ConfigStore

try:
    import tkinter as tk

    from krypto_waechter import gui
except ImportError:  # Python ohne Tkinter
    tk = None


class FakeNotifier:
    def __init__(self):
        self.sent = []

    def send(self, alerts):
        self.sent.extend(alerts)

    def deliver(self, alerts):
        return {"Windows": ""}


@unittest.skipIf(tk is None, "Tkinter ist nicht installiert")
class GuiTest(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(f"kein Bildschirm verfügbar: {exc}")
        self.tmp = tempfile.TemporaryDirectory()
        self.store = ConfigStore(Path(self.tmp.name) / "config.json")
        self.notifier = FakeNotifier()
        self.app = gui.App(self.root, self.store, self.notifier, demo=True, start_monitor=False,
                           alert_log=Path(self.tmp.name) / "alarme.csv")
        # Keine echten Meldungsfenster während der Tests
        patcher = mock.patch.multiple(gui.messagebox, showinfo=mock.DEFAULT, showerror=mock.DEFAULT,
                                      showwarning=mock.DEFAULT, askyesno=mock.DEFAULT)
        self.boxes = patcher.start()
        self.boxes["askyesno"].return_value = True
        self.addCleanup(patcher.stop)

    def tearDown(self):
        self.app.close()
        self.tmp.cleanup()

    def cycle(self, count=1):
        for _ in range(count):
            self.app.monitor.run_cycle()
            latest, alerts = None, []
            while not self.app.updates.empty():
                latest = self.app.updates.get_nowait()
                alerts += latest.alerts
            self.app.apply_snapshot(latest, alerts)
        self.root.update()

    def wait_for(self, condition, timeout=5.0):
        end = time.time() + timeout
        while time.time() < end:
            self.root.update()
            if condition():
                return True
            time.sleep(0.02)
        return False

    def rows(self, tree):
        return [tree.item(iid, "values") for iid in tree.get_children()]

    def test_tables_are_filled(self):
        self.cycle(10)
        watch = self.rows(self.app.watch_tree)
        self.assertEqual(len(watch), 6)
        self.assertEqual(watch[0][0], "Bitcoin (BTC)")
        self.assertTrue(watch[0][1].endswith(" €"))
        self.assertEqual(len(self.rows(self.app.scanner_tree)), 25)
        self.assertGreater(len(self.rows(self.app.alert_tree)), 0)
        self.assertTrue(self.app.notebook.tab(self.app.tab_alerts, "text").startswith("Alarme ("))
        self.assertIn("Noch keine Bestände", self.app.portfolio_summary.cget("text"))
        self.assertIn("Aktualisiert um", self.app.status.cget("text"))

    def test_portfolio_rows_and_totals(self):
        self.store.update(lambda cfg: cfg["coins"][0].update(amount=0.5, buy_price=40000))
        self.cycle()
        rows = self.rows(self.app.portfolio_tree)
        self.assertEqual([r[0] for r in rows], ["Bitcoin (BTC)", "Summe"])
        self.assertEqual(rows[0][1], "0,5")
        self.assertEqual(rows[0][2], "40.000,00 €")
        self.assertIn("Gesamtwert", self.app.portfolio_summary.cget("text"))

    def test_coin_dialog_search_and_save(self):
        dialog = gui.CoinDialog(self.app)
        dialog.query.set("sol")
        dialog.search()
        self.assertTrue(self.wait_for(lambda: dialog.selected))
        self.assertEqual(dialog.selected["id"], "solana")
        dialog.amount.set("2,5")
        dialog.buy.set("58.000")
        dialog.above.set("150")
        dialog.save()
        self.assertEqual(dialog.result, {"source": "coingecko", "id": "solana", "name": "Solana", "symbol": "SOL",
                                         "amount": 2.5, "buy_price": 58000.0, "alarm_above": 150.0,
                                         "alarm_below": None})

    def test_coin_dialog_rejects_bad_input(self):
        coin = self.store.get()["coins"][0]
        dialog = gui.CoinDialog(self.app, coin)
        dialog.amount.set("zwei")
        dialog.save()
        self.boxes["showerror"].assert_called_once()
        self.assertIsNone(dialog.result)
        dialog.amount.set("1")
        dialog.above.set("100")
        dialog.below.set("200")
        dialog.save()
        self.assertEqual(self.boxes["showerror"].call_count, 2)
        dialog.destroy()

    def test_editing_keeps_exact_values(self):
        self.store.update(lambda cfg: cfg["coins"][0].update(amount=0.12345678, buy_price=1234.5678))
        dialog = gui.CoinDialog(self.app, self.store.get()["coins"][0])
        dialog.save()
        self.assertEqual(dialog.result["amount"], 0.12345678)
        self.assertEqual(dialog.result["buy_price"], 1234.5678)

    def test_add_edit_move_remove(self):
        def fake_dialog(result):
            class FakeDialog(tk.Toplevel):
                def __init__(self, app, coin=None):
                    super().__init__(app.root)
                    self.result = result(coin) if callable(result) else result
                    self.after(10, self.destroy)
            return FakeDialog

        new = {"source": "binance", "id": "soleur", "name": "", "symbol": ""}
        with mock.patch.object(gui, "CoinDialog", fake_dialog(new)):
            self.app.add_coin()
        keys = [f"{c['source']}:{c['id']}" for c in self.store.get()["coins"]]
        self.assertEqual(keys[-1], "binance:SOLEUR")
        self.assertEqual(self.app.watch_tree.selection(), ("binance:SOLEUR",))

        with mock.patch.object(gui, "CoinDialog", fake_dialog(new)):
            self.app.add_coin()  # doppelt -> Hinweis statt zweitem Eintrag
        self.boxes["showinfo"].assert_called()
        self.assertEqual(len(self.store.get()["coins"]), 7)

        with mock.patch.object(gui, "CoinDialog", fake_dialog(lambda coin: dict(coin, amount=3.0))):
            self.app.edit_coin("binance:SOLEUR")
        self.assertEqual(self.store.get()["coins"][-1]["amount"], 3.0)

        self.app.move_coin(-1)
        self.assertEqual(self.store.get()["coins"][-2]["id"], "SOLEUR")
        self.app.remove_coin()
        self.assertNotIn("SOLEUR", [c["id"] for c in self.store.get()["coins"]])

    def test_add_from_scanner_and_sorting(self):
        self.cycle(2)
        self.app.sort_scanner("h24")
        changes = [float(row[4].split()[-2].replace(",", ".")) for row in self.rows(self.app.scanner_tree)
                   if row[4] != "–"]
        self.assertEqual(changes, sorted(changes, reverse=True))
        self.app.scanner_tree.selection_set("coingecko:pepe")
        self.app.add_from_scanner()
        self.assertIn("pepe", [c["id"] for c in self.store.get()["coins"]])

    def test_settings_dialog(self):
        dialog = gui.SettingsDialog(self.app)
        dialog.interval.set("120")
        dialog.rule_vars[0][1].set(True)
        dialog.rule_vars[0][2].set("2,5")
        dialog.tg_enabled.set(True)
        dialog.save()  # Telegram ohne Token -> Fehlermeldung
        self.boxes["showerror"].assert_called_once()
        self.assertIsNone(dialog.result)
        dialog.tg_enabled.set(False)
        dialog.pf_window.set("24 Std")
        dialog.save()
        cfg = self.store.update(dialog.result)
        self.assertEqual(cfg["poll_interval"], 120)
        self.assertEqual(cfg["move_rules"][0], {"minutes": 5, "percent": 2.5, "enabled": True})
        self.assertEqual(cfg["portfolio"]["move_minutes"], 1440)

    def test_buttons(self):
        self.app.toggle_pause()
        self.assertTrue(self.app.monitor.paused)
        self.assertEqual(self.app.pause_button.cget("text"), "Fortsetzen")
        self.app.toggle_pause()
        self.app.test_notification()
        self.assertTrue(self.wait_for(lambda: "Test gesendet" in self.app.status.cget("text")))
        self.assertEqual(len(self.rows(self.app.alert_tree)), 1)
        self.app.clear_alerts()
        self.assertEqual(self.rows(self.app.alert_tree), [])

    def test_close_remembers_window_size(self):
        self.root.geometry("1000x600")
        self.root.update()
        self.app.close()
        self.assertRegex(self.store.get()["window_geometry"], r"^\d+x\d+$")


if __name__ == "__main__":
    unittest.main()
