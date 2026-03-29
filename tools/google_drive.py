"""
tools/google_drive.py — Google Drive tools for Kiro.

Uses the drive.readonly scope already configured in google_auth.py.

Voice actions:
  - search_drive: "Find the budget spreadsheet in my Drive"
  - list_drive_folder: "What files are in my project folder?"
  - get_file_info: "When was the meeting notes doc last updated?"
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from .google_auth import get_google_service

logger = logging.getLogger("kiro")

# MIME type → friendly name
_MIME_NAMES = {
    "application/vnd.google-apps.document": "Google Doc",
    "application/vnd.google-apps.spreadsheet": "Google Sheet",
    "application/vnd.google-apps.presentation": "Google Slides",
    "application/vnd.google-apps.folder": "Folder",
    "application/vnd.google-apps.form": "Google Form",
    "application/pdf": "PDF",
    "image/jpeg": "Image (JPEG)",
    "image/png": "Image (PNG)",
    "text/plain": "Text file",
}


def _service():
    return get_google_service("drive", "v3")


def search_drive(query: str, max_results: int = 5, file_type: str = "") -> str:
    """Search Google Drive for files matching a query.
    Optional file_type: 'doc', 'sheet', 'slides', 'pdf', 'folder'."""
    try:
        svc = _service()

        # Build the Drive API query
        safe_query = query.replace("'", "\\'")
        q_parts = [f"name contains '{safe_query}'", "trashed=false"]

        type_map = {
            "doc": "application/vnd.google-apps.document",
            "docs": "application/vnd.google-apps.document",
            "sheet": "application/vnd.google-apps.spreadsheet",
            "sheets": "application/vnd.google-apps.spreadsheet",
            "slides": "application/vnd.google-apps.presentation",
            "presentation": "application/vnd.google-apps.presentation",
            "pdf": "application/pdf",
            "folder": "application/vnd.google-apps.folder",
        }
        if file_type and file_type.lower() in type_map:
            q_parts.append(f"mimeType='{type_map[file_type.lower()]}'")

        result = svc.files().list(
            q=" and ".join(q_parts),
            pageSize=max_results,
            fields="files(id, name, mimeType, modifiedTime, owners)",
            orderBy="modifiedTime desc",
        ).execute()

        files = result.get("files", [])
        if not files:
            return f"No files found matching '{query}'" + (f" of type {file_type}" if file_type else "") + "."

        lines = []
        for f in files:
            name = f.get("name", "Untitled")
            mime = f.get("mimeType", "")
            friendly = _MIME_NAMES.get(mime, mime.split("/")[-1] if "/" in mime else "File")
            modified = f.get("modifiedTime", "")[:10]
            lines.append(f"{name} ({friendly}, last modified {modified})")

        return f"Found {len(files)} file{'s' if len(files) != 1 else ''}: " + ". ".join(lines) + "."
    except Exception as e:
        logger.error("Drive search failed: %s", e)
        return f"Sorry, I couldn't search your Drive: {e}"


def list_drive_folder(folder_name: str = "", max_results: int = 10) -> str:
    """List files in a Google Drive folder. If no folder specified, lists recent files in root."""
    try:
        svc = _service()

        if folder_name:
            # Find the folder first
            safe_name = folder_name.replace("'", "\\'")
            folder_result = svc.files().list(
                q=f"name='{safe_name}' and mimeType='application/vnd.google-apps.folder' and trashed=false",
                pageSize=1,
                fields="files(id, name)",
            ).execute()
            folders = folder_result.get("files", [])
            if not folders:
                return f"I couldn't find a folder called '{folder_name}'."

            folder_id = folders[0]["id"]
            q = f"'{folder_id}' in parents and trashed=false"
            label = folder_name
        else:
            q = "'root' in parents and trashed=false"
            label = "your Drive root"

        result = svc.files().list(
            q=q,
            pageSize=max_results,
            fields="files(id, name, mimeType, modifiedTime)",
            orderBy="modifiedTime desc",
        ).execute()

        files = result.get("files", [])
        if not files:
            return f"No files found in {label}."

        lines = []
        for f in files:
            name = f.get("name", "Untitled")
            mime = f.get("mimeType", "")
            friendly = _MIME_NAMES.get(mime, "File")
            lines.append(f"{name} ({friendly})")

        return f"{len(files)} items in {label}: " + ". ".join(lines) + "."
    except Exception as e:
        logger.error("Drive list folder failed: %s", e)
        return f"Sorry, I couldn't list that folder: {e}"


def get_file_info(file_name: str) -> str:
    """Get metadata about a specific file in Google Drive (size, modified date, owner, type)."""
    try:
        svc = _service()
        safe_name = file_name.replace("'", "\\'")
        result = svc.files().list(
            q=f"name contains '{safe_name}' and trashed=false",
            pageSize=1,
            fields="files(id, name, mimeType, modifiedTime, createdTime, size, owners, shared, webViewLink)",
            orderBy="modifiedTime desc",
        ).execute()

        files = result.get("files", [])
        if not files:
            return f"No file found matching '{file_name}'."

        f = files[0]
        name = f.get("name", "Untitled")
        mime = f.get("mimeType", "")
        friendly = _MIME_NAMES.get(mime, mime.split("/")[-1] if "/" in mime else "File")
        modified = f.get("modifiedTime", "unknown")[:10]
        created = f.get("createdTime", "unknown")[:10]
        size = f.get("size")
        owners = f.get("owners", [])
        owner_name = owners[0].get("displayName", "unknown") if owners else "unknown"
        shared = "Yes" if f.get("shared") else "No"

        parts = [
            f"{name} is a {friendly}.",
            f"Created {created}, last modified {modified}.",
            f"Owner: {owner_name}. Shared: {shared}.",
        ]
        if size:
            size_mb = int(size) / (1024 * 1024)
            if size_mb >= 1:
                parts.append(f"Size: {size_mb:.1f} MB.")
            else:
                size_kb = int(size) / 1024
                parts.append(f"Size: {size_kb:.1f} KB.")

        return " ".join(parts)
    except Exception as e:
        logger.error("Drive file info failed: %s", e)
        return f"Sorry, I couldn't get info about that file: {e}"
