"""
tools/google_docs.py — Google Docs tools for Kiro.

Voice actions:
  - create_doc: "Create a document called meeting notes"
  - append_to_doc: "Add to the meeting notes: discussed timeline"
  - read_doc: "Read me the meeting notes"
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from .google_auth import get_google_service

logger = logging.getLogger("kiro")


def _docs_service():
    return get_google_service("docs", "v1")


def _drive_service():
    return get_google_service("drive", "v3")


def create_doc(title: str, initial_content: str = "") -> str:
    """Create a new Google Doc with optional initial content."""
    try:
        docs = _docs_service()
        doc = docs.documents().create(body={"title": title}).execute()
        doc_id = doc["documentId"]

        if initial_content:
            docs.documents().batchUpdate(
                documentId=doc_id,
                body={
                    "requests": [
                        {
                            "insertText": {
                                "location": {"index": 1},
                                "text": initial_content,
                            }
                        }
                    ]
                },
            ).execute()

        url = f"https://docs.google.com/document/d/{doc_id}"
        logger.info("Doc created: %s (%s)", title, url)
        return f"Document created: {title}."
    except Exception as e:
        logger.error("Docs create failed: %s", e)
        return f"Sorry, I couldn't create that document: {e}"


def append_to_doc(title: str, content: str) -> str:
    """Append text to an existing Google Doc (found by title search)."""
    try:
        doc_id = _find_doc(title)
        if not doc_id:
            return f"I couldn't find a document called '{title}'. Want me to create one?"

        docs = _docs_service()
        # Get current doc length to find the end index
        doc = docs.documents().get(documentId=doc_id).execute()
        body = doc.get("body", {})
        end_index = body.get("content", [{}])[-1].get("endIndex", 1) - 1
        if end_index < 1:
            end_index = 1

        docs.documents().batchUpdate(
            documentId=doc_id,
            body={
                "requests": [
                    {
                        "insertText": {
                            "location": {"index": end_index},
                            "text": "\n" + content,
                        }
                    }
                ]
            },
        ).execute()

        return f"Added to {title}."
    except Exception as e:
        logger.error("Docs append failed: %s", e)
        return f"Sorry, I couldn't update that document: {e}"


def read_doc(title: str, max_chars: int = 1500) -> str:
    """Read the content of a Google Doc (found by title search)."""
    try:
        doc_id = _find_doc(title)
        if not doc_id:
            return f"I couldn't find a document called '{title}'."

        docs = _docs_service()
        doc = docs.documents().get(documentId=doc_id).execute()

        # Extract plain text from the doc body
        text_parts = []
        for element in doc.get("body", {}).get("content", []):
            if "paragraph" in element:
                for pe in element["paragraph"].get("elements", []):
                    run = pe.get("textRun", {})
                    if "content" in run:
                        text_parts.append(run["content"])

        full_text = "".join(text_parts).strip()
        if not full_text:
            return f"The document '{title}' is empty."

        if len(full_text) > max_chars:
            full_text = full_text[:max_chars] + "... (truncated)"

        return f"From {title}: {full_text}"
    except Exception as e:
        logger.error("Docs read failed: %s", e)
        return f"Sorry, I couldn't read that document: {e}"


def _find_doc(title: str) -> str:
    """Find a Google Doc by title. Returns the document ID or empty string."""
    try:
        drive = _drive_service()
        # Escape single quotes in title
        safe_title = title.replace("'", "\\'")
        result = drive.files().list(
            q=f"name='{safe_title}' and mimeType='application/vnd.google-apps.document' and trashed=false",
            orderBy="modifiedTime desc",
            pageSize=1,
            fields="files(id, name)",
        ).execute()
        files = result.get("files", [])
        if files:
            return files[0]["id"]
        return ""
    except Exception as e:
        logger.warning("Drive search for doc '%s' failed: %s", title, e)
        return ""
