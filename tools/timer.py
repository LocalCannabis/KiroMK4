"""
tools/timer.py — Background timer that fires a callback when done.

Timers run in daemon threads. When a timer fires, it puts a spoken
notification into the orchestrator's notification_queue so Kiro can
announce it at the start of the next listen loop.
"""

from __future__ import annotations

import queue
import threading
from typing import Callable


def set_timer(
    seconds: int,
    label: str,
    notification_queue: queue.Queue,
    logger,
) -> str:
    """
    Start a background timer. When it fires, queues a spoken notification.
    Returns a human-readable confirmation string.
    """
    def _fire():
        msg = label if label else f"Your {_fmt(seconds)} timer is done."
        notification_queue.put(msg)
        logger.info("Timer fired: %s", msg)

    t = threading.Timer(seconds, _fire)
    t.daemon = True
    t.start()

    duration = _fmt(seconds)
    confirm = f"Timer set for {duration}" + (f": {label}." if label else ".")
    logger.info("Timer started: %ds — %s", seconds, label or "(unlabeled)")
    return confirm


def _fmt(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds} second{'s' if seconds != 1 else ''}"
    mins, secs = divmod(seconds, 60)
    out = f"{mins} minute{'s' if mins != 1 else ''}"
    if secs:
        out += f" and {secs} second{'s' if secs != 1 else ''}"
    return out
