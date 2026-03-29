"""
ambient/worker.py — Base worker class for all ambient intelligence workers.

Provides:
- Main loop with configurable sleep interval
- Graceful shutdown handling (SIGTERM/SIGINT)
- Error handling with backoff
- Heartbeat file for health checks
- Unified logging to both file and kiro_ambient_log table
- Database connection management

All ingestion and processing workers inherit from this.
"""

from __future__ import annotations

import logging
import os
import signal
import sys
import time
import traceback
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from .db import AmbientDB

logger = logging.getLogger("ambient.worker")

HEARTBEAT_DIR = Path(os.path.expanduser("~/.kiro/heartbeats"))


class BaseWorker(ABC):
    """
    Abstract base class for all ambient intelligence workers.

    Subclasses implement `process()` for their specific logic.
    The base class handles the run loop, shutdown, errors, and health.
    """

    # Subclasses must set these
    worker_name: str = "base_worker"
    default_interval_seconds: int = 300  # 5 minutes

    def __init__(self, interval_seconds: Optional[int] = None) -> None:
        self._running = False
        self._interval = interval_seconds or self.default_interval_seconds
        self._db: Optional[AmbientDB] = None
        self._consecutive_errors = 0
        self._max_backoff = 300  # 5 minute max backoff

        # Setup heartbeat dir
        HEARTBEAT_DIR.mkdir(parents=True, exist_ok=True)

        # Setup logging
        self._setup_logging()

    def _setup_logging(self) -> None:
        """Configure file + console logging for this worker."""
        log_dir = Path("./logs")
        log_dir.mkdir(exist_ok=True)

        log_file = log_dir / f"ambient_{self.worker_name}.log"
        formatter = logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

        file_handler = logging.FileHandler(log_file)
        file_handler.setFormatter(formatter)
        file_handler.setLevel(logging.DEBUG)

        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(formatter)
        console_handler.setLevel(logging.INFO)

        root = logging.getLogger()
        root.setLevel(logging.DEBUG)
        root.addHandler(file_handler)
        root.addHandler(console_handler)

    @property
    def db(self) -> AmbientDB:
        """Lazy-init database connection."""
        if self._db is None:
            self._db = AmbientDB()
        return self._db

    def _write_heartbeat(self) -> None:
        """Write a heartbeat file for health monitoring."""
        hb_file = HEARTBEAT_DIR / f"{self.worker_name}.heartbeat"
        hb_file.write_text(datetime.utcnow().isoformat())

    def _signal_handler(self, signum, frame) -> None:
        """Handle SIGTERM/SIGINT for graceful shutdown."""
        sig_name = signal.Signals(signum).name
        logger.info("%s received %s — shutting down gracefully", self.worker_name, sig_name)
        self._running = False

    def _backoff_sleep(self) -> float:
        """Calculate exponential backoff sleep time based on consecutive errors."""
        if self._consecutive_errors <= 0:
            return self._interval
        backoff = min(
            self._interval * (2 ** self._consecutive_errors),
            self._max_backoff,
        )
        logger.info("Backing off for %.1fs after %d consecutive errors",
                     backoff, self._consecutive_errors)
        return backoff

    def audit_log(self, level: str, message: str, metadata: Optional[Dict] = None) -> None:
        """Write to both Python logger and the kiro_ambient_log table."""
        log_fn = getattr(logger, level.lower(), logger.info)
        log_fn("[%s] %s", self.worker_name, message)
        try:
            self.db.log(self.worker_name, level.upper(), message, metadata)
        except Exception as e:
            logger.warning("Failed to write audit log to DB: %s", e)

    @abstractmethod
    def process(self) -> None:
        """
        Execute one cycle of this worker's logic.

        Called repeatedly by the run loop. Should be idempotent and
        handle its own error boundaries where possible.

        Raise exceptions for unexpected errors — the run loop will
        catch them, log, and backoff.
        """

    def setup(self) -> None:
        """
        Optional setup hook called once before the main loop starts.

        Override for one-time initialization (API client setup, etc.)
        """

    def cleanup(self) -> None:
        """
        Optional cleanup hook called after the main loop exits.

        Override for resource cleanup (close connections, etc.)
        """

    def run(self) -> None:
        """
        Main entry point. Sets up signal handlers and runs the process loop.

        Handles:
        - SIGTERM/SIGINT for graceful shutdown
        - Error backoff
        - Heartbeat writing
        - Audit logging
        """
        signal.signal(signal.SIGTERM, self._signal_handler)
        signal.signal(signal.SIGINT, self._signal_handler)

        self._running = True
        self.audit_log("INFO", f"{self.worker_name} starting (interval={self._interval}s)")

        try:
            self.setup()
        except Exception as e:
            self.audit_log("ERROR", f"Setup failed: {e}", {"traceback": traceback.format_exc()})
            return

        while self._running:
            try:
                self.process()
                self._consecutive_errors = 0
                self._write_heartbeat()
            except Exception as e:
                self._consecutive_errors += 1
                self.audit_log(
                    "ERROR",
                    f"Process error (attempt {self._consecutive_errors}): {e}",
                    {"traceback": traceback.format_exc()},
                )

            # Sleep (with backoff on errors), but check _running periodically
            sleep_time = self._backoff_sleep() if self._consecutive_errors > 0 else self._interval
            sleep_end = time.time() + sleep_time
            while self._running and time.time() < sleep_end:
                time.sleep(min(1.0, sleep_end - time.time()))

        # Cleanup
        try:
            self.cleanup()
        except Exception as e:
            logger.warning("Cleanup error: %s", e)

        if self._db:
            self._db.close()

        self.audit_log("INFO", f"{self.worker_name} stopped")
