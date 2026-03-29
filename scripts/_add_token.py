#!/usr/bin/env python3
"""One-shot: add work-imac token hash to DB, then self-deletes."""
import sys, os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "ui"))
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent / ".env")
except ImportError:
    pass

LABEL = "work-imac"
HASH  = "$2b$12$gj7OUfVgjLOKCVyGCFmSM.TAd/B/OClwoBqcLL6olaZEJGyO1PAJK"

from app import app
from models import KiroApiToken, db

with app.app_context():
    existing = KiroApiToken.query.filter_by(label=LABEL, revoked=False).first()
    if existing:
        print(f"Token '{LABEL}' already exists (id={existing.id}). Nothing added.")
        sys.exit(0)
    db.session.add(KiroApiToken(token_hash=HASH, label=LABEL))
    db.session.commit()
    row = KiroApiToken.query.filter_by(label=LABEL).order_by(KiroApiToken.id.desc()).first()
    print(f"✓ Token '{LABEL}' added — id={row.id}, created={row.created_at}")

os.unlink(__file__)
