"""
tools/gmail.py — Gmail tools for Kiro.

Voice actions:
  - read_emails: "Read my unread emails" / "Any new mail?"
  - read_email_content: "What did Dave's email say?"
  - send_email: "Send an email to Dave about the meeting"
  - reply_to_email: "Reply to Dave's email and say I'll be there"
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
            if "<" in sender:
                sender = sender.split("<")[0].strip().strip('"')
            summaries.append(f"From {sender}: {subject}")

        count = len(summaries)
        header = f"You have {count} unread email{'s' if count != 1 else ''}. " if "unread" in query else f"Found {count} email{'s' if count != 1 else ''}. "
        return header + ". ".join(summaries) + "."
    except Exception as e:
        logger.error("Gmail read failed: %s", e)
        return f"Sorry, I couldn't read your emails: {e}"


def read_email_content(search_query: str, max_chars: int = 1500) -> str:
    """Find and read the full body of a specific email by search query.
    Useful for 'what did Dave's email say?' or 'read me the email about the invoice'."""
    try:
        svc = _service()
        result = svc.users().messages().list(
            userId="me",
            q=search_query,
            maxResults=1,
        ).execute()

        messages = result.get("messages", [])
        if not messages:
            return f"No emails found matching '{search_query}'."

        msg = svc.users().messages().get(
            userId="me",
            id=messages[0]["id"],
            format="full",
        ).execute()

        headers = {h["name"]: h["value"] for h in msg.get("payload", {}).get("headers", [])}
        sender = headers.get("From", "Unknown")
        subject = headers.get("Subject", "No subject")
        date = headers.get("Date", "")
        if "<" in sender:
            sender = sender.split("<")[0].strip().strip('"')

        # Extract plain text body
        body_text = _extract_body(msg.get("payload", {}))
        if not body_text:
            body_text = msg.get("snippet", "(no readable content)")

        if len(body_text) > max_chars:
            body_text = body_text[:max_chars] + "... (truncated)"

        return f"From {sender}, subject: {subject}. {date}. Content: {body_text}"
    except Exception as e:
        logger.error("Gmail read content failed: %s", e)
        return f"Sorry, I couldn't read that email: {e}"


def _extract_body(payload: Dict) -> str:
    """Recursively extract plain text body from a Gmail message payload."""
    # Direct body
    if payload.get("mimeType") == "text/plain" and payload.get("body", {}).get("data"):
        return base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8", errors="replace")

    # Multipart — look for text/plain first, then text/html stripped
    parts = payload.get("parts", [])
    for part in parts:
        if part.get("mimeType") == "text/plain" and part.get("body", {}).get("data"):
            return base64.urlsafe_b64decode(part["body"]["data"]).decode("utf-8", errors="replace")

    # Recurse into multipart parts
    for part in parts:
        result = _extract_body(part)
        if result:
            return result

    return ""


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


def reply_to_email(search_query: str, body: str) -> str:
    """Reply to the most recent email matching the search query, preserving the thread."""
    try:
        svc = _service()
        result = svc.users().messages().list(
            userId="me",
            q=search_query,
            maxResults=1,
        ).execute()

        messages = result.get("messages", [])
        if not messages:
            return f"No emails found matching '{search_query}' to reply to."

        # Get the original message for threading info
        original = svc.users().messages().get(
            userId="me",
            id=messages[0]["id"],
            format="metadata",
            metadataHeaders=["From", "Subject", "Message-ID", "References", "In-Reply-To"],
        ).execute()

        headers = {h["name"]: h["value"] for h in original.get("payload", {}).get("headers", [])}
        original_from = headers.get("From", "")
        original_subject = headers.get("Subject", "")
        message_id = headers.get("Message-ID", "")
        thread_id = original.get("threadId", "")

        # Build reply-to address
        reply_to = original_from
        if "<" in reply_to:
            # Extract email from "Name <email@example.com>"
            reply_to = reply_to.split("<")[1].rstrip(">")

        # Build subject with Re: prefix
        reply_subject = original_subject
        if not reply_subject.lower().startswith("re:"):
            reply_subject = f"Re: {reply_subject}"

        # Build threaded reply
        reply = MIMEText(body)
        reply["to"] = reply_to
        reply["subject"] = reply_subject
        if message_id:
            reply["In-Reply-To"] = message_id
            reply["References"] = headers.get("References", "") + " " + message_id

        raw = base64.urlsafe_b64encode(reply.as_bytes()).decode("utf-8")
        send_body: Dict[str, Any] = {"raw": raw}
        if thread_id:
            send_body["threadId"] = thread_id

        svc.users().messages().send(userId="me", body=send_body).execute()

        sender_name = headers.get("From", reply_to)
        if "<" in sender_name:
            sender_name = sender_name.split("<")[0].strip().strip('"')

        return f"Reply sent to {sender_name} on: {original_subject}."
    except Exception as e:
        logger.error("Gmail reply failed: %s", e)
        return f"Sorry, I couldn't reply to that email: {e}"


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
