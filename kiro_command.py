#!/usr/bin/env python3
"""
kiro_command.py — Unified command centre for the Kiro AI system.

Replaces: go.sh, start_ui.sh, manual kiro_server.py launches.

Usage:
    python kiro_command.py start                # Full stack: UI + voice pipeline
    python kiro_command.py start --no-voice     # UI only (text chat, grow API, no STT/TTS)
    python kiro_command.py start --headless     # Voice API only (for Pi/macOS clients)
    python kiro_command.py migrate              # Run all persona DB migrations
    python kiro_command.py status               # Show running services
    python kiro_command.py stop                 # Clean shutdown
"""

from __future__ import annotations

import argparse
import logging
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

# Ensure project root is on sys.path
PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

# Load .env
try:
    from dotenv import load_dotenv
    load_dotenv(PROJECT_ROOT / ".env")
except ImportError:
    pass


def setup_logging(level: str = "INFO") -> logging.Logger:
    log_dir = PROJECT_ROOT / "logs"
    log_dir.mkdir(exist_ok=True)

    log_level = getattr(logging, level.upper(), logging.INFO)
    fmt = logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s")

    root = logging.getLogger()
    root.setLevel(log_level)
    root.handlers.clear()

    console = logging.StreamHandler()
    console.setFormatter(fmt)
    root.addHandler(console)

    from logging.handlers import RotatingFileHandler
    fh = RotatingFileHandler(
        log_dir / "kiro.log",
        maxBytes=10_485_760,
        backupCount=5,
        encoding="utf-8",
    )
    fh.setFormatter(fmt)
    root.addHandler(fh)

    return logging.getLogger("kiro")


# ============================================================================
# START — Unified server
# ============================================================================
def cmd_start(args):
    """Start the unified Kiro server."""
    logger = setup_logging(args.log_level)

    # Import here so logging is configured first
    os.chdir(str(PROJECT_ROOT))
    sys.path.insert(0, str(PROJECT_ROOT / "ui"))

    import yaml

    # Load voice pipeline config (used when --no-voice is not set)
    voice_cfg = {}
    voice_cfg_path = PROJECT_ROOT / "kiro_server_config.yaml"
    if voice_cfg_path.exists():
        with open(voice_cfg_path, encoding="utf-8") as f:
            voice_cfg = yaml.safe_load(f) or {}

    port = args.port or int(os.getenv("KIRO_UI_PORT", "5199"))
    host = args.host or "0.0.0.0"

    logger.info("=" * 60)
    logger.info("  KIRO COMMAND CENTRE")
    logger.info("=" * 60)
    logger.info("  Mode:    %s", "headless (voice API only)" if args.headless else
                ("full stack" if not args.no_voice else "UI only (no voice)"))
    logger.info("  Listen:  %s:%d", host, port)
    logger.info("=" * 60)

    if args.headless:
        # Headless mode: voice API only, no UI
        _start_headless(voice_cfg, host, port, logger)
    else:
        # Full or UI-only mode: import ui/app.py and optionally init voice
        _start_unified(voice_cfg, host, port, not args.no_voice, logger)


def _start_headless(voice_cfg, host, port, logger):
    """Voice API only — for Pi/macOS thin clients."""
    from flask import Flask
    app = Flask(__name__)
    app.config["start_time"] = time.time()

    # Voice pipeline
    from voice.routes import init_voice
    init_voice(app, voice_cfg)

    # Grow API
    try:
        from jack.grow_api import grow_bp
        app.register_blueprint(grow_bp, url_prefix="/api/grow")
        logger.info("Grow API registered")
    except Exception as e:
        logger.warning("Grow API unavailable: %s", e)

    logger.info("Starting headless voice server on %s:%d", host, port)
    app.run(host=host, port=port, debug=False, threaded=True)


def _start_unified(voice_cfg, host, port, enable_voice, logger):
    """Full stack: UI + optional voice pipeline, single Flask app, single port."""
    # The UI app is constructed by ui/app.py at import time.
    # We need to import it, then optionally bolt on the voice pipeline.
    from ui.app import app

    if enable_voice:
        logger.info("Initializing voice pipeline...")
        try:
            from voice.routes import init_voice
            init_voice(app, voice_cfg)
            logger.info("Voice pipeline active — /process, /health, /ping available")
        except Exception as e:
            logger.error("Voice pipeline failed to start: %s", e, exc_info=True)
            logger.warning("Continuing without voice — text chat still available")
    else:
        logger.info("Voice pipeline disabled (--no-voice)")

    logger.info("Starting unified Kiro server on %s:%d", host, port)
    app.run(host=host, port=port, debug=False, threaded=True, use_reloader=False)


# ============================================================================
# MIGRATE — Run all persona migrations
# ============================================================================
def cmd_migrate(args):
    """Run database migrations for all personas."""
    logger = setup_logging(args.log_level)
    os.chdir(str(PROJECT_ROOT))

    migration_modules = [
        ("jack", "jack.migrate"),
        ("finley", "finley.migrate"),
        ("coach", "coach.migrate"),
        ("ambient", "ambient.migrate"),
    ]

    success = 0
    for name, mod_path in migration_modules:
        logger.info("── Migrating: %s ──", name)
        try:
            mod = __import__(mod_path, fromlist=["main", "run_migrations"])
            # Prefer main() — it handles its own config loading
            if hasattr(mod, "main"):
                # Temporarily clear sys.argv so argparse inside main() doesn't choke
                saved_argv = sys.argv
                sys.argv = [mod_path]
                try:
                    mod.main()
                finally:
                    sys.argv = saved_argv
                logger.info("  ✓ %s migrations complete", name)
                success += 1
            else:
                logger.warning("  ? %s has no run_migrations() or main()", name)
        except ImportError as e:
            logger.info("  – %s not installed: %s", name, e)
        except Exception as e:
            logger.error("  ✗ %s migration failed: %s", name, e, exc_info=True)

    logger.info("Migrations complete: %d/%d modules", success, len(migration_modules))


# ============================================================================
# STATUS — Show what's running
# ============================================================================
def cmd_status(args):
    """Show status of Kiro services."""
    import urllib.request
    import json

    port = args.port or int(os.getenv("KIRO_UI_PORT", "5199"))
    print(f"\n  KIRO STATUS (port {port})")
    print("  " + "=" * 40)

    # Check unified server
    for path, label in [
        ("/api/health", "UI/Chat API"),
        ("/health", "Voice Pipeline"),
        ("/api/grow/readings", "Grow Sensor API"),
    ]:
        try:
            url = f"http://127.0.0.1:{port}{path}"
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=3) as resp:
                data = resp.read().decode()
                print(f"  ✓ {label:20s} — UP")
                if path == "/health":
                    try:
                        h = json.loads(data)
                        if h.get("tts_engine"):
                            print(f"    TTS: {h['tts_engine']}")
                    except Exception:
                        pass
        except Exception:
            print(f"  ✗ {label:20s} — DOWN")

    # Check ESP32 connectivity (latest reading)
    try:
        url = f"http://127.0.0.1:{port}/api/grow/readings"
        with urllib.request.urlopen(url, timeout=3) as resp:
            data = json.loads(resp.read())
            if data and data.get("recorded_at"):
                print(f"  ✓ {'ESP32 Sensor Data':20s} — Last reading: {data['recorded_at']}")
            else:
                print(f"  – {'ESP32 Sensor Data':20s} — No readings yet")
    except Exception:
        pass

    print()


# ============================================================================
# STOP — Clean shutdown
# ============================================================================
def cmd_stop(args):
    """Stop running Kiro processes."""
    import signal

    pids_killed = 0

    # Find kiro processes (but not this script)
    try:
        result = subprocess.run(
            ["pgrep", "-f", "kiro_command.py start|kiro_server.py|ui/app.py"],
            capture_output=True, text=True,
        )
        pids = [int(p) for p in result.stdout.strip().split("\n") if p]
        my_pid = os.getpid()
        for pid in pids:
            if pid != my_pid:
                try:
                    os.kill(pid, signal.SIGTERM)
                    print(f"  Stopped PID {pid}")
                    pids_killed += 1
                except ProcessLookupError:
                    pass
    except Exception:
        pass

    if pids_killed == 0:
        print("  No Kiro processes found.")
    else:
        print(f"  Stopped {pids_killed} process(es).")


# ============================================================================
# CLI
# ============================================================================
def main():
    parser = argparse.ArgumentParser(
        prog="kiro",
        description="Kiro Command Centre — unified launcher for the Kiro AI system",
    )
    sub = parser.add_subparsers(dest="command", help="Available commands")

    # -- start --
    p_start = sub.add_parser("start", help="Start the Kiro server")
    p_start.add_argument("--no-voice", action="store_true",
                         help="Disable voice pipeline (text chat + grow API only)")
    p_start.add_argument("--headless", action="store_true",
                         help="Voice API only, no web UI (for thin clients)")
    p_start.add_argument("--host", type=str, default=None)
    p_start.add_argument("--port", type=int, default=None)
    p_start.add_argument("--log-level", type=str, default="INFO")
    p_start.set_defaults(func=cmd_start)

    # -- migrate --
    p_migrate = sub.add_parser("migrate", help="Run all database migrations")
    p_migrate.add_argument("--log-level", type=str, default="INFO")
    p_migrate.set_defaults(func=cmd_migrate)

    # -- status --
    p_status = sub.add_parser("status", help="Check status of running services")
    p_status.add_argument("--port", type=int, default=None)
    p_status.set_defaults(func=cmd_status)

    # -- stop --
    p_stop = sub.add_parser("stop", help="Stop running Kiro processes")
    p_stop.set_defaults(func=cmd_stop)

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    args.func(args)


if __name__ == "__main__":
    main()
