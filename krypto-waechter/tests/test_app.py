import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import helpers  # noqa: F401  (setzt den Suchpfad)

from krypto_waechter import __version__, app, winutils

LAUNCHER = Path(__file__).resolve().parent.parent / "Krypto-Waechter.pyw"


def run_launcher(*args):
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    return subprocess.run([sys.executable, str(LAUNCHER), *args], capture_output=True, text=True, encoding="utf-8",
                          env=env, timeout=120)


def has_display() -> bool:
    return sys.platform == "win32" or bool(os.environ.get("DISPLAY"))


class AppTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.config = str(Path(self.tmp.name) / "config.json")

    def tearDown(self):
        self.tmp.cleanup()

    def test_arguments(self):
        args = app.parse_args(["--konsole", "--demo", "--intervall", "30"])
        self.assertTrue(args.konsole and args.demo)
        self.assertEqual(args.intervall, 30)
        self.assertFalse(app.parse_args([]).minimiert)

    def test_console_demo_once(self):
        result = run_launcher("--konsole", "--demo", "--einmal", "--config", self.config)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Konsolenmodus", result.stdout)
        self.assertIn("Bitcoin (BTC)", result.stdout)
        self.assertIn("€", result.stdout)
        self.assertTrue(Path(self.config).exists())
        self.assertTrue((Path(self.tmp.name) / "krypto-waechter.log").exists())

    def test_version(self):
        result = run_launcher("--version")
        self.assertEqual(result.returncode, 0)
        self.assertIn(__version__, result.stdout)

    @unittest.skipUnless(has_display(), "kein Bildschirm verfügbar")
    def test_selftest(self):
        result = run_launcher("--selbsttest", "--config", self.config)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Selbsttest erfolgreich", result.stdout)

    def test_icon_is_installed(self):
        target = app.install_icon(Path(self.tmp.name) / "neu")
        self.assertTrue(target.exists())
        self.assertEqual(target.read_bytes()[:8], b"\x89PNG\r\n\x1a\n")

    def test_autostart_command(self):
        command = winutils.launch_command()
        self.assertIn(f'"{LAUNCHER}"', command)
        self.assertTrue(command.endswith(" --minimiert"))
        self.assertTrue(command.startswith('"'))
        self.assertEqual(winutils.program_command()[-1], str(LAUNCHER))

    def test_demo_uses_its_own_settings(self):
        folder = Path(self.tmp.name)
        (folder / "config.json").write_text('{"currency": "usd"}', encoding="utf-8")
        path = app.demo_config(folder)
        self.assertEqual(path, folder / "demo" / "config.json")
        self.assertIn("usd", path.read_text(encoding="utf-8"))
        path.write_text('{"currency": "chf"}', encoding="utf-8")
        app.demo_config(folder)  # wird nicht erneut überschrieben
        self.assertIn("chf", path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
