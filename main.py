"""Start GREEN:  python main.py"""

import sys

if sys.version_info < (3, 11):
    sys.exit("GREEN requires Python 3.11 or newer (3.12 recommended).")

try:
    import PySide6  # noqa: F401
except ImportError:
    sys.exit("PySide6 is not installed. Run:  pip install -r requirements.txt")

from green.app.application import run

if __name__ == "__main__":
    sys.exit(run())
