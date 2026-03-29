#!/usr/bin/env python3
"""
ambient/ingest/ingest_feeds.py — RSS/news feed ingestion worker.

Polls configured RSS feeds every 1-2 hours and writes articles
as kiro_events with source='feed'.

Targets cannabis industry, BC government regulatory, BCLDB, and
relevant Reddit feeds.

Usage:
    python -m ambient.ingest.ingest_feeds
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from ambient.worker import BaseWorker

logger = logging.getLogger("ambient.ingest.feeds")

# Default RSS feeds — configurable via kiro_ambient_config
DEFAULT_FEEDS = [
    # Cannabis industry
    {"name": "Leafly News", "url": "https://www.leafly.com/news/feed", "category": "industry"},
    {"name": "MJBizDaily", "url": "https://mjbizdaily.com/feed/", "category": "industry"},
    {"name": "Cannabis Retailer", "url": "https://cannabisretailer.ca/feed/", "category": "retail"},
    # BC Government / Regulatory
    {"name": "BC Gov Cannabis", "url": "https://news.gov.bc.ca/feed", "category": "regulatory"},
    # Reddit
    {"name": "r/canadients", "url": "https://www.reddit.com/r/canadients/.rss", "category": "community"},
    {"name": "r/BCcannabis", "url": "https://www.reddit.com/r/BCcannabis/.rss", "category": "community"},
    # Cultivation
    {"name": "GrowWeedEasy", "url": "https://www.growweedeasy.com/feed", "category": "cultivation"},
]


class FeedIngestionWorker(BaseWorker):
    """
    Polls RSS/Atom feeds for new articles and ingests them
    as kiro_events for ambient processing.
    """

    worker_name = "ingest_feeds"
    default_interval_seconds = 3600  # 1 hour

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._feeds: List[Dict] = []
        self._feedparser = None

    def setup(self) -> None:
        """Load feed list and initialize feedparser."""
        try:
            import feedparser
            self._feedparser = feedparser
        except ImportError:
            raise RuntimeError(
                "feedparser not installed. Run: pip install feedparser"
            )

        # Load feeds from config or use defaults
        custom_feeds = self.db.get_config("rss_feeds")
        self._feeds = custom_feeds if isinstance(custom_feeds, list) else DEFAULT_FEEDS

        # Load polling interval
        polling = self.db.get_config("stream_polling", {})
        if isinstance(polling, dict) and "feeds" in polling:
            self._interval = int(polling["feeds"])

        self.audit_log("INFO", f"Feed ingestion initialized ({len(self._feeds)} feeds)")

    def _generate_source_id(self, entry: Any, feed_name: str) -> str:
        """Generate a unique source_id for a feed entry."""
        # Try entry ID first, then link, then hash of title
        entry_id = getattr(entry, "id", None) or getattr(entry, "link", None)
        if entry_id:
            return f"feed_{hashlib.md5(entry_id.encode()).hexdigest()[:16]}"
        title = getattr(entry, "title", "")
        return f"feed_{hashlib.md5(f'{feed_name}:{title}'.encode()).hexdigest()[:16]}"

    def _parse_date(self, entry: Any) -> datetime:
        """Parse publication date from a feed entry."""
        for attr in ("published_parsed", "updated_parsed"):
            parsed = getattr(entry, attr, None)
            if parsed:
                try:
                    return datetime(*parsed[:6], tzinfo=timezone.utc)
                except (TypeError, ValueError):
                    pass
        return datetime.now(timezone.utc)

    def process(self) -> None:
        """Poll all configured feeds for new articles."""
        total_ingested = 0

        for feed_config in self._feeds:
            feed_name = feed_config.get("name", "unknown")
            feed_url = feed_config.get("url", "")
            feed_category = feed_config.get("category", "general")

            if not feed_url:
                continue

            try:
                feed = self._feedparser.parse(feed_url)
            except Exception as e:
                logger.warning("Failed to parse feed %s: %s", feed_name, e)
                continue

            if feed.bozo and not feed.entries:
                logger.debug("Feed %s returned no entries (bozo=%s)", feed_name, feed.bozo)
                continue

            for entry in feed.entries[:20]:  # Cap at 20 entries per feed
                source_id = self._generate_source_id(entry, feed_name)
                occurred_at = self._parse_date(entry)

                title = getattr(entry, "title", "")
                link = getattr(entry, "link", "")
                author = getattr(entry, "author", "")
                summary = getattr(entry, "summary", "")

                # Truncate summary for storage
                if len(summary) > 2000:
                    summary = summary[:2000] + "..."

                metadata = {
                    "feed_name": feed_name,
                    "feed_url": feed_url,
                    "feed_category": feed_category,
                    "title": title,
                    "link": link,
                    "author": author,
                    "tags": [
                        t.get("term", "") for t in getattr(entry, "tags", [])
                    ],
                }

                raw_content = f"{title}\n\n{summary}" if summary else title

                event_id = self.db.insert_event(
                    source="feed",
                    source_id=source_id,
                    event_type="article",
                    occurred_at=occurred_at,
                    metadata=metadata,
                    raw_content=raw_content[:5000] if raw_content else None,
                )
                if event_id:
                    total_ingested += 1

            # Small delay between feeds to be polite
            time.sleep(1)

        if total_ingested > 0:
            self.audit_log("INFO", f"Ingested {total_ingested} feed articles", {
                "count": total_ingested,
                "feeds_checked": len(self._feeds),
            })
        else:
            logger.debug("No new feed articles across %d feeds", len(self._feeds))


def main():
    worker = FeedIngestionWorker()
    worker.run()


if __name__ == "__main__":
    main()
