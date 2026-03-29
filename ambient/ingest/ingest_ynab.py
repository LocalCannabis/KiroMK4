#!/usr/bin/env python3
"""
ambient/ingest/ingest_ynab.py — YNAB transaction ingestion worker.

Polls the YNAB API via delta sync (server_knowledge parameter) and writes
new transactions as kiro_events with source='ynab'.

Uses Finley's existing YNABClient and config for authentication.
Polling interval: 30 minutes (configurable via kiro_ambient_config).

Usage:
    python -m ambient.ingest.ingest_ynab
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from ambient.worker import BaseWorker

logger = logging.getLogger("ambient.ingest.ynab")


class YNABIngestionWorker(BaseWorker):
    """
    Polls YNAB for new transactions and budget changes.

    Delta sync via server_knowledge prevents re-fetching unchanged data.
    Each transaction becomes a kiro_event with source='ynab'.
    """

    worker_name = "ingest_ynab"
    default_interval_seconds = 1800  # 30 minutes

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._ynab_client = None
        self._finley_config = None

    def setup(self) -> None:
        """Initialize YNAB client from Finley's config."""
        from finley.config import load_config as load_finley_config
        from finley.ynab_client import YNABClient

        self._finley_config = load_finley_config()
        token = self._finley_config.get("ynab_token", "")
        plan_id = self._finley_config.get("plan_id", "")

        if not token:
            raise RuntimeError(
                "YNAB token not configured. Set it in ~/.kiro/finley_config.json"
            )

        self._ynab_client = YNABClient(token=token, plan_id=plan_id)

        # Ensure plan ID is discovered
        discovered_plan = self._ynab_client.ensure_plan_id()
        if not plan_id and discovered_plan:
            self._finley_config["plan_id"] = discovered_plan
            from finley.config import save_config
            save_config(self._finley_config)

        # Load polling interval from ambient config
        polling = self.db.get_config("stream_polling", {})
        if isinstance(polling, dict) and "ynab" in polling:
            self._interval = int(polling["ynab"])

        self.audit_log("INFO", f"YNAB ingestion initialized (plan={self._ynab_client.plan_id})")

    def process(self) -> None:
        """Fetch new transactions and budget data via delta sync."""
        sk = self._finley_config.get("last_server_knowledge", {})

        # ── Transactions ──────────────────────────────────────────────
        txn_sk = sk.get("transactions")
        since_date = (datetime.utcnow() - timedelta(days=30)).strftime("%Y-%m-%d")
        try:
            transactions, new_sk = self._ynab_client.get_transactions(
                since_date=since_date,
                last_knowledge=txn_sk,
            )
        except Exception as e:
            self.audit_log("ERROR", f"YNAB transaction fetch failed: {e}")
            return

        ingested = 0
        for txn in transactions:
            event_id = self.db.insert_event(
                source="ynab",
                source_id=f"txn_{txn['id']}",
                event_type="transaction",
                occurred_at=datetime.strptime(txn["date"], "%Y-%m-%d") if txn.get("date") else datetime.utcnow(),
                metadata={
                    "transaction_id": txn["id"],
                    "amount": txn.get("amount", 0),
                    "amount_dollars": txn.get("amount", 0) / 1000.0,
                    "payee_name": txn.get("payee_name"),
                    "category_name": txn.get("category_name"),
                    "account_name": txn.get("account_name"),
                    "memo": txn.get("memo"),
                    "approved": txn.get("approved"),
                    "cleared": txn.get("cleared"),
                    "flag_color": txn.get("flag_color"),
                    "subtransactions": txn.get("subtransactions"),
                },
                raw_content=json.dumps(txn),
            )
            if event_id:
                ingested += 1

        # ── Scheduled Transactions ────────────────────────────────────
        sched_sk = sk.get("scheduled_transactions")
        try:
            scheduled, sched_new_sk = self._ynab_client.get_scheduled_transactions(
                last_knowledge=sched_sk,
            )
        except Exception as e:
            self.audit_log("WARNING", f"YNAB scheduled transactions fetch failed: {e}")
            scheduled, sched_new_sk = [], sched_sk

        for sched in scheduled:
            self.db.insert_event(
                source="ynab",
                source_id=f"sched_{sched['id']}",
                event_type="scheduled_transaction",
                occurred_at=datetime.strptime(sched.get("date_next", sched.get("date_first", "")), "%Y-%m-%d")
                    if sched.get("date_next") or sched.get("date_first")
                    else datetime.utcnow(),
                metadata={
                    "scheduled_id": sched["id"],
                    "amount": sched.get("amount", 0),
                    "amount_dollars": sched.get("amount", 0) / 1000.0,
                    "frequency": sched.get("frequency"),
                    "payee_name": sched.get("payee_name"),
                    "category_name": sched.get("category_name"),
                    "date_next": sched.get("date_next"),
                },
                raw_content=json.dumps(sched),
            )

        # ── Update server_knowledge ───────────────────────────────────
        sk["transactions"] = new_sk
        if sched_new_sk is not None:
            sk["scheduled_transactions"] = sched_new_sk
        self._finley_config["last_server_knowledge"] = sk
        from finley.config import save_config
        save_config(self._finley_config)

        if ingested > 0:
            self.audit_log("INFO", f"Ingested {ingested} new YNAB transactions", {
                "transactions": ingested,
                "scheduled": len(scheduled),
                "server_knowledge": sk,
            })
        else:
            logger.debug("No new YNAB transactions")


def main():
    worker = YNABIngestionWorker()
    worker.run()


if __name__ == "__main__":
    main()
