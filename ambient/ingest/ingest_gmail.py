#!/usr/bin/env python3
"""
ambient/ingest/ingest_gmail.py — Gmail ingestion worker.

Polls the Gmail API every 10 minutes for new emails and writes them
as kiro_events with source='gmail'.

Privacy rule: stores email metadata and full body for processing,
but the content purger will clear raw_content after processing.

Usage:
    python -m ambient.ingest.ingest_gmail
"""

from __future__ import annotations

import base64
import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from ambient.worker import BaseWorker

logger = logging.getLogger("ambient.ingest.gmail")


class GmailIngestionWorker(BaseWorker):
    """
    Polls Gmail for new messages in the inbox.

    Uses the Gmail API to fetch recent messages, extract metadata
    and body text, and store as kiro_events.
    """

    worker_name = "ingest_gmail"
    default_interval_seconds = 600  # 10 minutes

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._service = None

    def setup(self) -> None:
        """Initialize Gmail API service."""
        from tools.google_auth import get_google_service
        self._service = get_google_service("gmail", "v1")

        # Load polling interval from ambient config
        polling = self.db.get_config("stream_polling", {})
        if isinstance(polling, dict) and "gmail" in polling:
            self._interval = int(polling["gmail"])

        self.audit_log("INFO", "Gmail ingestion initialized")

    def _extract_body(self, payload: Dict) -> str:
        """Extract plain text body from Gmail message payload."""
        body_text = ""

        if payload.get("mimeType") == "text/plain":
            data = payload.get("body", {}).get("data", "")
            if data:
                body_text = base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
        elif payload.get("parts"):
            for part in payload["parts"]:
                if part.get("mimeType") == "text/plain":
                    data = part.get("body", {}).get("data", "")
                    if data:
                        body_text = base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
                        break
                elif part.get("parts"):
                    # Nested multipart
                    body_text = self._extract_body(part)
                    if body_text:
                        break

        return body_text

    def _get_header(self, headers: List[Dict], name: str) -> str:
        """Get a specific header value from Gmail headers list."""
        for h in headers:
            if h.get("name", "").lower() == name.lower():
                return h.get("value", "")
        return ""

    def process(self) -> None:
        """Fetch recent Gmail messages."""
        try:
            # Get recent messages (last 50, inbox only)
            results = self._service.users().messages().list(
                userId="me",
                maxResults=50,
                labelIds=["INBOX"],
            ).execute()
        except Exception as e:
            self.audit_log("ERROR", f"Gmail API list error: {e}")
            return

        messages = results.get("messages", [])
        if not messages:
            logger.debug("No messages in inbox")
            return

        ingested = 0
        for msg_ref in messages:
            msg_id = msg_ref["id"]

            # Check if already ingested (fast path — avoid full API call)
            existing = self.db.insert_event(
                source="gmail",
                source_id=f"gmail_{msg_id}",
                event_type="email",
                occurred_at=datetime.now(timezone.utc),  # Placeholder, updated below
                metadata={},
                raw_content=None,
            )
            if existing is None:
                # Already ingested
                continue

            # Fetch full message
            try:
                msg = self._service.users().messages().get(
                    userId="me",
                    id=msg_id,
                    format="full",
                ).execute()
            except Exception as e:
                logger.warning("Failed to fetch message %s: %s", msg_id, e)
                continue

            headers = msg.get("payload", {}).get("headers", [])
            subject = self._get_header(headers, "Subject")
            sender = self._get_header(headers, "From")
            to = self._get_header(headers, "To")
            date_str = self._get_header(headers, "Date")

            # Parse date
            try:
                from email.utils import parsedate_to_datetime
                occurred_at = parsedate_to_datetime(date_str)
            except Exception:
                internal_ts = msg.get("internalDate", "0")
                occurred_at = datetime.fromtimestamp(int(internal_ts) / 1000, tz=timezone.utc)

            # Extract body
            body = self._extract_body(msg.get("payload", {}))

            labels = msg.get("labelIds", [])
            snippet = msg.get("snippet", "")

            metadata = {
                "message_id": msg_id,
                "thread_id": msg.get("threadId", ""),
                "subject": subject,
                "from": sender,
                "to": to,
                "labels": labels,
                "snippet": snippet,
                "has_attachments": any(
                    p.get("filename") for p in msg.get("payload", {}).get("parts", [])
                ),
            }

            # Update the placeholder event with real data
            conn = self.db._conn()
            try:
                with conn.cursor() as cur:
                    cur.execute("""
                        UPDATE kiro_events
                        SET occurred_at = %s, metadata = %s::jsonb, raw_content = %s
                        WHERE source = 'gmail' AND source_id = %s
                    """, (occurred_at, json.dumps(metadata), body[:10000] if body else None,
                          f"gmail_{msg_id}"))
                conn.commit()
            finally:
                self.db._put(conn)

            ingested += 1

        if ingested > 0:
            self.audit_log("INFO", f"Ingested {ingested} new emails", {
                "count": ingested,
                "total_checked": len(messages),
            })


def main():
    worker = GmailIngestionWorker()
    worker.run()


if __name__ == "__main__":
    main()
