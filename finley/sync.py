"""
finley/sync.py — Background sync daemon for YNAB data.

Pulls YNAB data into the PostgreSQL kiro database on a configurable
interval (default 30 min). Uses delta requests (server_knowledge) to
minimize API calls and stay well under the 200 req/hour rate limit.

After each sync, runs the transaction classifier and financial profiler
to keep Tim's financial profile current.

Can run as:
  - A background thread inside Kiro's main process (start_sync_thread)
  - A one-shot CLI call for cron or testing (run_sync_once)
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, Optional

from .config import (
    load_config,
    save_config,
    get_server_knowledge,
    set_server_knowledge,
)
from .db import FinleyDB, format_currency
from .ynab_client import YNABClient, YNABClientError

logger = logging.getLogger("kiro.finley.sync")

# Endpoints we sync, in order. Each entry maps to a YNABClient method
# and a FinleyDB upsert method.
_SYNC_ENDPOINTS = [
    {
        "name": "accounts",
        "fetch": "get_accounts",
        "upsert": "upsert_accounts",
    },
    {
        "name": "categories",
        "fetch": "get_categories",
        "upsert": "upsert_categories",
    },
    {
        "name": "transactions",
        "fetch": "get_transactions",
        "upsert": "upsert_transactions",
    },
    {
        "name": "scheduled_transactions",
        "fetch": "get_scheduled_transactions",
        "upsert": "upsert_scheduled_transactions",
    },
]


def run_sync_once(
    cfg: Dict[str, Any] | None = None,
    db: FinleyDB | None = None,
    post_sync_callback: Callable[[FinleyDB, Dict[str, Any]], None] | None = None,
) -> Dict[str, int]:
    """
    Execute a single sync pass against the YNAB API.

    Returns a dict of {endpoint_name: records_updated}.

    On first run (no server_knowledge), does a full initial pull:
      - Transactions: last 12 months via since_date
      - Everything else: full pull

    Subsequent runs use delta sync via server_knowledge.
    """
    if cfg is None:
        cfg = load_config()
    if db is None:
        db = FinleyDB()

    token = cfg.get("ynab_token", "")
    if not token:
        logger.error("No YNAB token configured. Set it in ~/.kiro/finley_config.json")
        return {}

    client = YNABClient(token=token, plan_id=cfg.get("plan_id", ""))

    # Auto-discover plan_id if not set
    if not cfg.get("plan_id"):
        plan_id = client.ensure_plan_id()
        cfg["plan_id"] = plan_id
        save_config(cfg)
        logger.info("Stored plan_id: %s", plan_id)

    results: Dict[str, int] = {}
    is_initial = not bool(cfg.get("last_server_knowledge", {}))

    for endpoint in _SYNC_ENDPOINTS:
        ep_name = endpoint["name"]
        fetch_method = getattr(client, endpoint["fetch"])
        upsert_method = getattr(db, endpoint["upsert"])

        last_sk = get_server_knowledge(cfg, ep_name)

        try:
            # Build kwargs for the fetch call
            kwargs: Dict[str, Any] = {}
            if last_sk is not None:
                kwargs["last_knowledge"] = last_sk

            # For transactions: use since_date on initial pull (12 months)
            # and on delta pulls (90 days as safety floor)
            if ep_name == "transactions":
                if is_initial:
                    since = (datetime.utcnow() - timedelta(days=365)).strftime("%Y-%m-%d")
                    logger.info("Initial sync — pulling transactions since %s", since)
                else:
                    since = (datetime.utcnow() - timedelta(days=90)).strftime("%Y-%m-%d")
                kwargs["since_date"] = since

            data, new_sk = fetch_method(**kwargs)

            count = upsert_method(data)
            set_server_knowledge(cfg, ep_name, new_sk)
            db.log_sync(ep_name, new_sk, count)

            results[ep_name] = count
            logger.info(
                "Synced %s: %d records (sk: %s → %s)",
                ep_name, count,
                last_sk or "initial", new_sk,
            )

        except YNABClientError as exc:
            logger.error("Sync failed for %s: %s", ep_name, exc)
            results[ep_name] = -1
        except Exception as exc:
            logger.error("Unexpected error syncing %s: %s", ep_name, exc, exc_info=True)
            results[ep_name] = -1

    # Persist updated server_knowledge values
    save_config(cfg)

    total = sum(v for v in results.values() if v > 0)
    logger.info("Sync complete: %d total records across %d endpoints", total, len(results))

    # Post-sync callback (used for generating proactive insights)
    if post_sync_callback:
        try:
            post_sync_callback(db, cfg)
        except Exception as exc:
            logger.error("Post-sync callback failed: %s", exc, exc_info=True)

    return results


# ---------------------------------------------------------------------------
# Background thread
# ---------------------------------------------------------------------------

class SyncDaemon:
    """
    Runs YNAB sync on a recurring interval in a background thread.

    Usage:
        daemon = SyncDaemon(interval_minutes=30, post_sync_callback=generate_insights)
        daemon.start()
        ...
        daemon.stop()
    """

    def __init__(
        self,
        interval_minutes: int = 30,
        post_sync_callback: Callable[[FinleyDB, Dict[str, Any]], None] | None = None,
        run_on_start: bool = True,
    ):
        self._interval = interval_minutes * 60  # seconds
        self._callback = post_sync_callback
        self._run_on_start = run_on_start
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_sync_result: Dict[str, int] = {}

    def _loop(self):
        """Main sync loop — runs in the background thread."""
        logger.info(
            "Finley sync daemon started (interval=%dmin)", self._interval // 60
        )

        if self._run_on_start:
            logger.info("Running initial sync...")
            self._last_sync_result = run_sync_once(
                post_sync_callback=self._callback
            )

        while not self._stop_event.is_set():
            # Sleep in small increments so we can respond to stop quickly
            slept = 0
            while slept < self._interval and not self._stop_event.is_set():
                time.sleep(min(10, self._interval - slept))
                slept += 10

            if self._stop_event.is_set():
                break

            try:
                self._last_sync_result = run_sync_once(
                    post_sync_callback=self._callback
                )
            except Exception as exc:
                logger.error("Sync daemon iteration failed: %s", exc, exc_info=True)

        logger.info("Finley sync daemon stopped.")

    def start(self):
        """Start the sync daemon in a background thread."""
        if self._thread and self._thread.is_alive():
            logger.warning("Sync daemon already running.")
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._loop, name="finley-sync", daemon=True
        )
        self._thread.start()

    def stop(self):
        """Signal the daemon to stop. Non-blocking."""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=15)

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def last_result(self) -> Dict[str, int]:
        return self._last_sync_result


# ---------------------------------------------------------------------------
# Default post-sync pipeline: classify → profile → engage
# ---------------------------------------------------------------------------

def default_post_sync(db: FinleyDB, cfg: Dict[str, Any]) -> None:
    """
    Standard post-sync pipeline:
      1. Classify unclassified transactions
      2. Build/update financial profile
      3. Check for proactive engagements
    """
    from .classifier import classify_payee
    from .profiler import build_profile
    from .engagement import check_engagements

    # 1. Classify new transactions
    unclassified = db.get_unclassified_transactions()
    if unclassified:
        classified_count = 0
        for txn in unclassified:
            payee = txn.get("payee_name", "")
            if not payee:
                continue
            result = classify_payee(payee)
            db.update_transaction_classification(
                txn_id=txn["id"],
                classified_category=result["category"],
                classified_subcategory=result.get("subcategory"),
                classified_merchant_type=result.get("merchant_type"),
            )
            classified_count += 1
        logger.info("Classified %d/%d transactions", classified_count, len(unclassified))

    # 2. Build financial profile
    try:
        profile = build_profile(db)
        logger.info("Profile updated — stage: %s", profile.get("stage", "unknown"))
    except Exception as exc:
        logger.error("Profile build failed: %s", exc, exc_info=True)
        profile = db.get_latest_profile() or {}

    # 3. Check proactive engagements
    try:
        engagements = check_engagements(db, profile)
        if engagements:
            logger.info("Fired %d proactive engagement(s)", len(engagements))
    except Exception as exc:
        logger.error("Engagement check failed: %s", exc, exc_info=True)


# ---------------------------------------------------------------------------
# CLI entry point for manual / cron sync
# ---------------------------------------------------------------------------

def main():
    """Run a one-shot sync from the command line."""
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )

    cfg = load_config()
    if not cfg.get("ynab_token"):
        print("ERROR: No YNAB token set.")
        print(f"Edit ~/.kiro/finley_config.json and add your Personal Access Token.")
        sys.exit(1)

    results = run_sync_once(cfg, post_sync_callback=default_post_sync)

    print("\nSync results:")
    for ep, count in results.items():
        status = f"{count} records" if count >= 0 else "FAILED"
        print(f"  {ep}: {status}")


if __name__ == "__main__":
    main()
