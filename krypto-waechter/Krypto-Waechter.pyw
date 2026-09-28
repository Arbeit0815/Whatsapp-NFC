"""Startet den Krypto-Wächter – unter Windows per Doppelklick, ohne Konsolenfenster."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from krypto_waechter.app import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
