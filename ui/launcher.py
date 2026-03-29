"""
KIRO UI — Launcher

Starts the Flask server and opens the browser.
No pywebview dependency — just a browser tab.

Usage:
    python launcher.py              # start + open browser
    python launcher.py --headless   # start only (no browser)
    python launcher.py --port 5200  # override port
"""

from __future__ import annotations

import argparse
import threading
import time
import webbrowser

from app import app
from config import FLASK_PORT


def main():
    parser = argparse.ArgumentParser(description="KIRO Chat UI")
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Start the server without opening a browser window",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=FLASK_PORT,
        help=f"Port to run on (default: {FLASK_PORT})",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Host to bind to (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Run Flask in debug mode",
    )
    args = parser.parse_args()

    url = f"http://{args.host}:{args.port}"

    if not args.headless:
        def _open():
            time.sleep(1.2)  # give Flask a moment to bind
            webbrowser.open(url)

        threading.Thread(target=_open, daemon=True).start()

    print(f"\n  KIRO UI → {url}\n")
    app.run(host=args.host, port=args.port, debug=args.debug)


if __name__ == "__main__":
    main()
