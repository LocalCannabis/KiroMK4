"""
tools/gmail.py — Gmail tools for Kiro.

Voice actions:
  - read_emails: "Read my unread emails" / "Any new mail?"
  - send_email: "Send an email to Dave about the meeting"
  - draft_email: "Draft an email to Mom"
  - search_emails: "Find emails about the invoice"
"""

from __future__ import annotations

import base64
import logging
from email.mime.text import MIMEText
from typing import Any, Dict, List, Optional

from .google_auth import get_google_service

logger = logging.getLogger("kiro")


def _service():
    return get_google_service("gmail", "v1")


def read_emails(max_results: int = 5, query: str = "is:unread") -> str:
    """Read recent emails. Defaults to unread inbox."""
    try:
        svc = _service()
        result = svc.users().messages().list(
            userId="me",
            q=query,
            maxResults=max_results,
        ).execute()

        messages = result.get("messages", [])
        if not messages:
            return "No unread emails right now." if "unread" in query else "No emails found for that search."

        summaries = []
        for msg_meta in messages[:max_results]:
            msg = svc.users().messages().get(
                userId="me",
                id=msg_meta["id"],
                format="metadata",
                metadataHeaders=["From", "Subject", "Date"],
            ).execute()
            headers = {h["name"]: h["value"] for h in msg.get("payload", {}).get("headers", [])}
            sender = headers.get("From", "Unknown")
            subject = headers.get("Subject", "No subject")
            # Clean sender — extract just the name if possible
            if "<" in sender:
                sender = sender.split("<")[0].strip().strip('"')
            summaries.append(f"From {sender}: {subject}")

        count = len(summaries)
        header = f"You have {count} unread email{'s' if count != 1 else ''}. " if "unread" in query else f"Found {count} email{'s' if count != 1 else ''}. "
        return header + ". ".join(summaries) + "."
    except Exception as e:
        logger.error("Gmail read failed: %s", e)
        return f"Sorry, I couldn't read your emails: {e}"


def send_email(to: str, subject: str, body: str) -> str:
    """Send an email via Gmail."""
    try:
        svc = _service()
        message = MIMEText(body)
        message["to"] = to
        message["subject"] = subject

        raw = base64.urlsafe_b64encode(message.as_bytes()).decode("utf-8")
        svc.users().messages().send(
            userId="me",
            body={"raw": raw},
        ).execute()

        return f"Email sent to {to} with subject: {subject}."
    except Exception as e:
        logger.error("Gmail send failed: %s", e)
        return f"Sorry, I couldn't send that email: {e}"


def draft_email(to: str, subject: str, body: str) -> str:
    """Create a draft email in Gmail."""
    try:
        svc = _service()
        message = MIMEText(body)
        message["to"] = to
        message["subject"] = subject

        raw = base64.urlsafe_b64encode(message.as_bytes()).decode("utf-8")
        svc.users().drafts().create(
            userId="me",
            body={"message": {"raw": raw}},
        ).execute()

        return f"Draft created for {to} with subject: {subject}."
    except Exception as e:
        logger.error("Gmail draft failed: %s", e)
        return f"Sorry, I couldn't create that draft: {e}"


def search_emails(query: str, max_results: int = 5) -> str:
    """Search Gmail with a query string."""
    return read_emails(max_results=max_results, query=query)
