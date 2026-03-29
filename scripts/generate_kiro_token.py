#!/usr/bin/env python3
"""
Generate a new Kiro API token for a remote client (e.g., work iMac).

Usage:
    python scripts/generate_kiro_token.py [label]
    python scripts/generate_kiro_token.py "work-imac"

Output:
    - Plaintext token  → copy to macOS Keychain on the client
    - bcrypt hash      → insert into kiro_api_tokens on the Beast

The plaintext is shown ONCE. The Beast only ever stores the hash.

To add to the database (run from the KiroMK4 project root):
    conda run -n kiro_asr python -c "
    import sys; sys.path.insert(0, 'ui')
    from app import app
    from models import KiroApiToken, db
    with app.app_context():
        db.session.add(KiroApiToken(token_hash='PASTE_HASH_HERE', label='PASTE_LABEL_HERE'))
        db.session.commit()
        print('Token added.')
    "

Or directly in psql:
    INSERT INTO kiro_api_tokens (token_hash, label)
    VALUES ('PASTE_HASH_HERE', 'PASTE_LABEL_HERE');

To revoke a token:
    UPDATE kiro_api_tokens SET revoked = TRUE WHERE label = 'work-imac';
"""

import sys
import secrets

try:
    import bcrypt
except ImportError:
    print("Error: bcrypt not installed. Run: pip install bcrypt")
    sys.exit(1)

label = sys.argv[1] if len(sys.argv) > 1 else "unnamed"
token = secrets.token_urlsafe(32)
token_hash = bcrypt.hashpw(token.encode(), bcrypt.gensalt()).decode()

print()
print(f"  Label : {label}")
print(f"  Token : {token}")
print(f"          ↑ SAVE THIS — shown once. Copy to Mac Keychain.")
print()
print(f"  Hash  : {token_hash}")
print(f"          ↑ Store this in the Beast DB.")
print()
print("  Quick-add to Beast DB:")
print(f"    conda run -n kiro_asr python scripts/generate_kiro_token.py --add \"{label}\" \"{token_hash}\"")
print()

# If called with --add label hash, insert into DB directly
if len(sys.argv) == 4 and sys.argv[1] == "--add":
    import os
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ui"))

    try:
        from dotenv import load_dotenv
        load_dotenv(Path(__file__).resolve().parent.parent / ".env")
    except ImportError:
        pass

    label_arg = sys.argv[2]
    hash_arg = sys.argv[3]

    from app import app
    from models import KiroApiToken, db

    with app.app_context():
        existing = KiroApiToken.query.filter_by(label=label_arg, revoked=False).first()
        if existing:
            print(f"  Warning: active token for label '{label_arg}' already exists (id={existing.id})")
            print("  Revoke it first or use a different label.")
            sys.exit(1)
        db.session.add(KiroApiToken(token_hash=hash_arg, label=label_arg))
        db.session.commit()
        print(f"  ✓ Token for '{label_arg}' added to database.")
