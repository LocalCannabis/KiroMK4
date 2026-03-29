"""
KIRO UI — Bearer Token Authentication

Localhost requests (127.0.0.1 / ::1) are always allowed — the GTK overlay
and dev tooling live there and need no token.

Remote requests (iMac over Tailscale, etc.) must present a valid Bearer
token that matches a non-revoked row in kiro_api_tokens (bcrypt verified).

Usage in app.py:
    from auth import init_auth
    init_auth(app)

This wires a before_request hook that covers all /api/* routes.
Page routes (/, /hud) are never gated.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

log = logging.getLogger("kiro.auth")

_LOCALHOST = {"127.0.0.1", "::1", "::ffff:127.0.0.1"}


def init_auth(app):
    """Register the before_request auth hook on the Flask app."""

    @app.before_request
    def _check_remote_auth():
        from flask import request, jsonify

        # Only gate /api/* — page routes are open
        if not request.path.startswith("/api/"):
            return None

        # Local overlay / dev: always through
        if request.remote_addr in _LOCALHOST:
            return None

        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            log.warning("Remote request to %s without token from %s",
                        request.path, request.remote_addr)
            return jsonify({"error": "unauthorized"}), 401

        raw_token = auth_header.split(" ", 1)[1].strip()
        if not _validate_token(raw_token):
            log.warning("Invalid token from %s for %s",
                        request.remote_addr, request.path)
            return jsonify({"error": "unauthorized"}), 401

        return None


def _validate_token(raw_token: str) -> bool:
    """
    Verify raw_token against bcrypt hashes in kiro_api_tokens.
    Updates last_used_at on a successful match.
    Returns True if valid, False otherwise.
    """
    try:
        import bcrypt
        from models import KiroApiToken, db

        rows = KiroApiToken.query.filter_by(revoked=False).all()
        for row in rows:
            if bcrypt.checkpw(raw_token.encode(), row.token_hash.encode()):
                row.last_used_at = datetime.now(timezone.utc)
                db.session.commit()
                log.debug("Token validated for label=%s", row.label)
                return True
    except ImportError:
        log.error("bcrypt not installed — pip install bcrypt")
    except Exception as exc:
        log.error("Token validation error: %s", exc)
    return False
