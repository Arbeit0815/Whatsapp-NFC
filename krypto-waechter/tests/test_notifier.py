import base64
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest import mock

from helpers import FakeRequest

from krypto_waechter import notifier
from krypto_waechter.config import ConfigStore
from krypto_waechter.engine import Alert
from krypto_waechter.net import NetError


def alert(title="Bitcoin fällt: -5,00 % in 1 Std", message="Kurs: 50.000,00 €", direction="down"):
    return Alert(0.0, "move", direction, "coingecko:bitcoin", title, message, icon="📉")


class ToastTest(unittest.TestCase):
    def test_xml_is_valid_and_ascii_only(self):
        xml = notifier.build_toast_xml("Bitcoin's <Kurs> & \"mehr\" 📈 fällt", "Zeile 1\nZeile 2\nZeile 3\x07")
        self.assertTrue(xml.isascii())
        self.assertNotIn("'", xml)
        root = ET.fromstring(xml)
        texts = [t.text for t in root.iter("text")]
        self.assertEqual(texts, ["Bitcoin's <Kurs> & \"mehr\" 📈 fällt", "Zeile 1", "Zeile 2 · Zeile 3"])
        self.assertEqual(root.find("audio").get("silent"), "true")

    def test_script_embeds_xml_safely(self):
        xml = notifier.build_toast_xml("Tëst", "Ä")
        script = notifier.build_toast_script(xml, "Krypto'App")
        self.assertIn(f"$xml.LoadXml('{xml}')", script)
        self.assertIn("CreateToastNotifier('Krypto''App').Show($toast)", script)
        self.assertNotIn(".Show(", notifier.build_toast_script(xml, "x", show=False))

    def test_encoded_command_round_trip(self):
        script = notifier.build_toast_script(notifier.build_toast_xml("äöü €", "x"), notifier.POWERSHELL_APP_ID)
        encoded = notifier.encode_powershell(script)
        self.assertEqual(base64.b64decode(encoded).decode("utf-16-le"), script)

    def test_clean_error_reads_clixml(self):
        raw = '#< CLIXML\r\n<Objs><S S="Error">Fehler beim Laden_x000D__x000A_</S><S S="Error">Zeile 2</S></Objs>'
        self.assertEqual(notifier._clean_error(raw), "Fehler beim Laden Zeile 2")

    @unittest.skipUnless(sys.platform == "win32", "nur unter Windows")
    def test_powershell_can_build_the_toast(self):
        """Lädt die Windows-Runtime-Typen und prüft das XML – ohne die Meldung wirklich anzuzeigen."""
        xml = notifier.build_toast_xml("Test äöü € 📈", "Kurs: 1.234,56 €\n24 Std: +5,00 %")
        ok, error = notifier.run_powershell(notifier.build_toast_script(xml, notifier.POWERSHELL_APP_ID, show=False))
        self.assertTrue(ok, error)

    @unittest.skipUnless(sys.platform == "win32", "nur unter Windows")
    def test_powershell_reports_errors(self):
        ok, error = notifier.run_powershell("throw 'absichtlicher Fehler'")
        self.assertFalse(ok)
        self.assertIn("absichtlicher Fehler", error)


class TelegramTest(unittest.TestCase):
    def test_send(self):
        request = FakeRequest({"ok": True})
        notifier.send_telegram(" 123:ABC ", " 42 ", "Hallo", request=request)
        call = request.calls[0]
        self.assertEqual(call["url"], "https://api.telegram.org/bot123:ABC/sendMessage")
        self.assertEqual(call["data"]["chat_id"], "42")
        self.assertEqual(call["data"]["text"], "Hallo")

    def test_send_requires_settings(self):
        with self.assertRaises(NetError):
            notifier.send_telegram("", "42", "x", request=FakeRequest({}))

    def test_helpful_errors(self):
        cases = [(NetError("HTTP 401: Unauthorized", status=401), "Bot-Token"),
                 (NetError("HTTP 400: Bad Request: chat not found", status=400), "Chat-ID"),
                 (NetError("HTTP 403: Forbidden: bot was blocked by the user", status=403), "blockiert")]
        for error, expected in cases:
            with self.subTest(expected=expected), self.assertRaises(NetError) as caught:
                notifier.send_telegram("t", "1", "x", request=FakeRequest(error))
            self.assertIn(expected, str(caught.exception))
            self.assertNotIn("t/sendMessage", str(caught.exception))

    def test_find_chat_id(self):
        updates = {"ok": True, "result": [
            {"update_id": 1, "message": {"chat": {"id": 111, "type": "private"}, "text": "alt"}},
            {"update_id": 2, "message": {"chat": {"id": 222, "type": "private"}, "text": "Hallo"}},
        ]}
        self.assertEqual(notifier.find_telegram_chat_id("t", request=FakeRequest(updates)), "222")
        self.assertIsNone(notifier.find_telegram_chat_id("t", request=FakeRequest({"ok": True, "result": []})))

    def test_text(self):
        text = notifier.telegram_text([alert(), alert("Zweiter", "Details")])
        self.assertEqual(text, "📉 Bitcoin fällt: -5,00 % in 1 Std\nKurs: 50.000,00 €\n\n📉 Zweiter\nDetails")


class NotifierTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = ConfigStore(Path(self.tmp.name) / "config.json")

    def tearDown(self):
        self.tmp.cleanup()

    def configure(self, **values):
        self.store.update(lambda cfg: cfg["notifications"].update(values))

    def test_all_channels_on_windows(self):
        self.configure(telegram_enabled=True, telegram_token="t", telegram_chat_id="1")
        with mock.patch.object(notifier.sys, "platform", "win32"), \
                mock.patch.object(notifier, "show_toast", return_value=(True, "")) as toast, \
                mock.patch.object(notifier, "register_app_id") as register, \
                mock.patch.object(notifier, "play_sound") as sound, \
                mock.patch.object(notifier, "send_telegram") as telegram:
            results = notifier.Notifier(self.store).deliver([alert()])
        self.assertEqual(results, {"Windows": "", "Ton": "", "Telegram": ""})
        toast.assert_called_once_with("📉 Bitcoin fällt: -5,00 % in 1 Std", "Kurs: 50.000,00 €", "KryptoWaechter")
        register.assert_called_once()
        sound.assert_called_once_with("down")
        self.assertEqual(telegram.call_args[0][:2], ("t", "1"))

    def test_many_alerts_become_one_toast(self):
        alerts = [alert(f"Coin {i}", direction="up") for i in range(6)]
        with mock.patch.object(notifier.sys, "platform", "win32"), \
                mock.patch.object(notifier, "show_toast", return_value=(True, "")) as toast, \
                mock.patch.object(notifier, "register_app_id"), mock.patch.object(notifier, "play_sound") as sound:
            notifier.Notifier(self.store).deliver(alerts)
        toast.assert_called_once()
        title, message, _ = toast.call_args[0]
        self.assertEqual(title, "6 Krypto-Alarme")
        self.assertIn("und 2 weitere", message)
        sound.assert_called_once_with("up")

    def test_falls_back_to_powershell_sender(self):
        calls = []

        def fake_toast(title, message, app_id):
            calls.append(app_id)
            return (app_id == notifier.POWERSHELL_APP_ID), "Element nicht gefunden"

        with mock.patch.object(notifier.sys, "platform", "win32"), \
                mock.patch.object(notifier, "show_toast", side_effect=fake_toast), \
                mock.patch.object(notifier, "register_app_id"), mock.patch.object(notifier, "play_sound"):
            results = notifier.Notifier(self.store).deliver([alert()])
        self.assertEqual(calls, ["KryptoWaechter", notifier.POWERSHELL_APP_ID])
        self.assertEqual(results["Windows"], "")

    def test_registration_failure_uses_powershell_sender(self):
        with mock.patch.object(notifier.sys, "platform", "win32"), \
                mock.patch.object(notifier, "show_toast", return_value=(True, "")) as toast, \
                mock.patch.object(notifier, "register_app_id", side_effect=OSError("Zugriff verweigert")), \
                mock.patch.object(notifier, "play_sound"):
            instance = notifier.Notifier(self.store)
            instance.deliver([alert()])
            instance.deliver([alert()])
        self.assertEqual([c[0][2] for c in toast.call_args_list], [notifier.POWERSHELL_APP_ID] * 2)

    def test_compat_mode_and_errors_are_reported(self):
        self.configure(toast_compat=True, sound=False)
        with mock.patch.object(notifier.sys, "platform", "win32"), \
                mock.patch.object(notifier, "show_toast", return_value=(False, "kaputt")) as toast:
            results = notifier.Notifier(self.store).deliver([alert()])
        self.assertEqual(toast.call_args[0][2], notifier.POWERSHELL_APP_ID)
        self.assertEqual(results, {"Windows": "kaputt"})

    def test_background_delivery_and_flush(self):
        delivered = []
        instance = notifier.Notifier(self.store)
        with mock.patch.object(instance, "deliver", side_effect=lambda alerts: delivered.append(alerts)):
            instance.send([alert()])
            instance.send([])
            self.assertTrue(instance.flush(timeout=5))
        self.assertEqual(len(delivered), 1)

    def test_nothing_enabled_on_other_systems(self):
        with mock.patch.object(notifier.sys, "platform", "linux"):
            self.assertEqual(notifier.Notifier(self.store).deliver([alert()]), {})


if __name__ == "__main__":
    unittest.main()
