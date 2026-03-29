#!/usr/bin/env python3
"""
ambient/process/purger.py — Content Purger processing worker.

Privacy by design: clears raw_content from processed events after
insights have been extracted. Gmail bodies are purged after 72 hours
(configurable). Kiro keeps the insight, not the surveillance.

Runs every 6 hours.

Usage:
    python -m ambient.process.purger
"""

from __future__ import annotations

import logging
from typing import Any, Dict

from ambient.worker import BaseWorker

logger = logging.getLogger("ambient.process.purger")

# Sources that should have raw_content purged after processing
SENSITIVE_SOURCES = ["gmail"]


class ContentPurgerWorker(BaseWorker):
    """
    Clears raw_content from sensitive source events after insights
    have been extracted. Respects privacy — stores insights, not data.
    """

    worker_name = "process_purger"
    default_interval_seconds = 21600  # 6 hours

    def process(self) -> None:
        """Purge raw content from sensitive sources."""
        purge_hours = self.db.get_config("content_purge_after_hours", 72)
        if not isinstance(purge_hours, (int, float)):
            purge_hours = 72

        total_purged = 0
        for source in SENSITIVE_SOURCES:
            count = self.db.purge_old_content(source=source, hours=int(purge_hours))
            if count > 0:
                total_purged += count
                self.audit_log("INFO", f"Purged {count} {source} events (raw_content cleared)", {
                    "source": source,
                    "count": count,
                    "threshold_hours": purge_hours,
                })

        if total_purged > 0:
            self.audit_log("INFO", f"Total content purged: {total_purged} events")
        else:
            logger.debug("No content to purge")


def main():
    worker = ContentPurgerWorker()
    worker.run()


if __name__ == "__main__":
    main()
