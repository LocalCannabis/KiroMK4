"""
tools/google_auth.py — Shared Google OAuth2 credential management.

First run opens a browser for consent.  After that, the token is cached
at credentials/token.json and refreshed automatically.

Usage:
    from tools.google_auth import get_google_service
    cal = get_google_service("calendar", "v3")
    sheets = get_google_service("sheets", "v4")
    gmail = get_google_service("gmail", "v1")
    docs = get_google_service("docs", "v1")
"""

from __future__ import annotations

import os
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

# All scopes Kiro needs — requested once on first auth.
_SCOPES = [
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/documents",
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.readonly",
]

_CREDS_DIR = Path(__file__).resolve().parent.parent / "credentials"
_CLIENT_SECRET = _CREDS_DIR / "google_credentials.json"
_TOKEN_FILE = _CREDS_DIR / "token.json"

_creds_cache: Credentials | None = None


def _get_credentials() -> Credentials:
    """Load or create OAuth2 credentials, refreshing if expired."""
    global _creds_cache

    if _creds_cache and _creds_cache.valid:
        return _creds_cache

    creds: Credentials | None = None

    if _TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(str(_TOKEN_FILE), _SCOPES)

    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
    elif not creds or not creds.valid:
        if not _CLIENT_SECRET.exists():
            raise FileNotFoundError(
                f"Google credentials not found at {_CLIENT_SECRET}. "
                "Download OAuth client JSON from Google Cloud Console."
            )
        flow = InstalledAppFlow.from_client_secrets_file(str(_CLIENT_SECRET), _SCOPES)
        creds = flow.run_local_server(port=0)

    # Persist token for next startup
    _TOKEN_FILE.write_text(creds.to_json())
    _creds_cache = creds
    return creds


def get_google_service(api: str, version: str):
    """Return an authenticated googleapiclient service object."""
    creds = _get_credentials()
    return build(api, version, credentials=creds, cache_discovery=False)
