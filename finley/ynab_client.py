"""
finley/ynab_client.py — Thin wrapper around the official YNAB Python SDK.

Handles authentication, delta requests via server_knowledge, and
translates SDK objects into plain dicts for SQLite storage.

Never logs or prints the access token.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("kiro.finley.ynab")


class YNABClientError(Exception):
    """Raised when YNAB API interaction fails."""


class YNABClient:
    """
    Wraps the official `ynab` Python SDK with delta-sync awareness.

    Usage:
        client = YNABClient(token="...", plan_id="...")
        accounts, sk = client.get_accounts(last_knowledge=42)
    """

    def __init__(self, token: str, plan_id: str = ""):
        if not token:
            raise YNABClientError("YNAB token is required. Set it in ~/.kiro/finley_config.json")

        try:
            import ynab
        except ImportError:
            raise YNABClientError(
                "YNAB SDK not installed. Run: pip install ynab"
            )

        self._ynab = ynab
        self._config = ynab.Configuration(access_token=token)
        self._plan_id = plan_id
        logger.info("YNAB client initialized (plan_id=%s)", plan_id or "<not set>")

    # ------------------------------------------------------------------
    # Plan discovery
    # ------------------------------------------------------------------

    def get_plans(self) -> list[dict]:
        """Fetch all plans (budgets). Returns list of {id, name, ...}."""
        with self._ynab.ApiClient(self._config) as api:
            plans_api = self._ynab.PlansApi(api)
            resp = plans_api.get_plans()
            return [
                {"id": p.id, "name": p.name}
                for p in resp.data.plans
            ]

    def ensure_plan_id(self) -> str:
        """Return stored plan_id, or discover and return the first available one."""
        if self._plan_id:
            return self._plan_id
        plans = self.get_plans()
        if not plans:
            raise YNABClientError("No YNAB plans found for this token.")
        self._plan_id = plans[0]["id"]
        logger.info("Auto-discovered plan: %s (id=%s)", plans[0]["name"], self._plan_id)
        return self._plan_id

    @property
    def plan_id(self) -> str:
        return self._plan_id

    @plan_id.setter
    def plan_id(self, value: str):
        self._plan_id = value

    # ------------------------------------------------------------------
    # Accounts
    # ------------------------------------------------------------------

    def get_accounts(self, last_knowledge: int | None = None) -> Tuple[list[dict], int]:
        """
        Fetch accounts. Returns (accounts_list, server_knowledge).
        Pass last_knowledge for delta sync.
        """
        pid = self.ensure_plan_id()
        with self._ynab.ApiClient(self._config) as api:
            accounts_api = self._ynab.AccountsApi(api)
            kwargs = {}
            if last_knowledge is not None:
                kwargs["last_knowledge_of_server"] = last_knowledge
            resp = accounts_api.get_accounts(pid, **kwargs)
            accounts = [
                {
                    "id": str(a.id),
                    "name": a.name,
                    "type": a.type if hasattr(a, "type") else "",
                    "on_budget": getattr(a, "on_budget", False),
                    "closed": getattr(a, "closed", False),
                    "balance": getattr(a, "balance", 0),
                    "cleared_balance": getattr(a, "cleared_balance", 0),
                    "uncleared_balance": getattr(a, "uncleared_balance", 0),
                }
                for a in resp.data.accounts
            ]
            sk = getattr(resp.data, "server_knowledge", 0)
            return accounts, sk

    # ------------------------------------------------------------------
    # Categories
    # ------------------------------------------------------------------

    def get_categories(self, last_knowledge: int | None = None) -> Tuple[list[dict], int]:
        """
        Fetch all category groups and their categories.
        Returns (flat category list, server_knowledge).
        """
        pid = self.ensure_plan_id()
        with self._ynab.ApiClient(self._config) as api:
            categories_api = self._ynab.CategoriesApi(api)
            kwargs = {}
            if last_knowledge is not None:
                kwargs["last_knowledge_of_server"] = last_knowledge
            resp = categories_api.get_categories(pid, **kwargs)
            cats = []
            for group in resp.data.category_groups:
                for cat in group.categories:
                    cats.append({
                        "id": str(cat.id),
                        "group_id": str(group.id),
                        "group_name": group.name,
                        "name": cat.name,
                        "budgeted": getattr(cat, "budgeted", 0),
                        "activity": getattr(cat, "activity", 0),
                        "balance": getattr(cat, "balance", 0),
                        "goal_type": getattr(cat, "goal_type", None),
                        "goal_target": getattr(cat, "goal_target", None),
                        "goal_target_date": getattr(cat, "goal_target_date", None),
                    })
            sk = getattr(resp.data, "server_knowledge", 0)
            return cats, sk

    # ------------------------------------------------------------------
    # Transactions
    # ------------------------------------------------------------------

    def get_transactions(
        self,
        since_date: str | None = None,
        last_knowledge: int | None = None,
    ) -> Tuple[list[dict], int]:
        """
        Fetch transactions. Uses since_date as a floor filter and
        last_knowledge for delta sync.
        Returns (transactions_list, server_knowledge).
        """
        pid = self.ensure_plan_id()
        with self._ynab.ApiClient(self._config) as api:
            txn_api = self._ynab.TransactionsApi(api)
            kwargs = {}
            if since_date:
                kwargs["since_date"] = since_date
            if last_knowledge is not None:
                kwargs["last_knowledge_of_server"] = last_knowledge
            resp = txn_api.get_transactions(pid, **kwargs)
            txns = [
                {
                    "id": str(t.id),
                    "date": str(getattr(t, "var_date", "")),
                    "amount": t.amount,
                    "memo": getattr(t, "memo", None),
                    "payee_id": str(getattr(t, "payee_id", None)) if getattr(t, "payee_id", None) else None,
                    "payee_name": getattr(t, "payee_name", None),
                    "category_id": str(getattr(t, "category_id", None)) if getattr(t, "category_id", None) else None,
                    "category_name": getattr(t, "category_name", None),
                    "account_id": str(getattr(t, "account_id", None)),
                    "account_name": getattr(t, "account_name", None),
                    "approved": getattr(t, "approved", False),
                    "cleared": getattr(t, "cleared", "uncleared"),
                    "flag_color": getattr(t, "flag_color", None),
                    "transfer_account_id": str(getattr(t, "transfer_account_id", None)) if getattr(t, "transfer_account_id", None) else None,
                    "subtransactions": [
                        {
                            "id": str(s.id),
                            "amount": s.amount,
                            "memo": getattr(s, "memo", None),
                            "payee_name": getattr(s, "payee_name", None),
                            "category_name": getattr(s, "category_name", None),
                        }
                        for s in (getattr(t, "subtransactions", None) or [])
                    ] or None,
                }
                for t in resp.data.transactions
            ]
            sk = getattr(resp.data, "server_knowledge", 0)
            return txns, sk

    # ------------------------------------------------------------------
    # Scheduled transactions
    # ------------------------------------------------------------------

    def get_scheduled_transactions(
        self, last_knowledge: int | None = None
    ) -> Tuple[list[dict], int]:
        """Fetch scheduled/recurring transactions."""
        pid = self.ensure_plan_id()
        with self._ynab.ApiClient(self._config) as api:
            sched_api = self._ynab.ScheduledTransactionsApi(api)
            kwargs = {}
            if last_knowledge is not None:
                kwargs["last_knowledge_of_server"] = last_knowledge
            resp = sched_api.get_scheduled_transactions(pid, **kwargs)
            items = [
                {
                    "id": str(s.id),
                    "date_first": str(getattr(s, "var_date_first", "")),
                    "date_next": str(getattr(s, "var_date_next", "")),
                    "frequency": getattr(s, "frequency", ""),
                    "amount": getattr(s, "amount", 0),
                    "payee_name": getattr(s, "payee_name", None),
                    "category_name": getattr(s, "category_name", None),
                    "memo": getattr(s, "memo", None),
                }
                for s in resp.data.scheduled_transactions
            ]
            sk = getattr(resp.data, "server_knowledge", 0)
            return items, sk

    # ------------------------------------------------------------------
    # Month detail
    # ------------------------------------------------------------------

    def get_month(self, month: str) -> dict:
        """
        Fetch a single month's budget detail.
        month should be YYYY-MM-01 format.
        Returns dict with 'categories' list and summary fields.
        """
        pid = self.ensure_plan_id()
        with self._ynab.ApiClient(self._config) as api:
            months_api = self._ynab.MonthsApi(api)
            resp = months_api.get_budget_month(pid, month)
            m = resp.data.month
            return {
                "month": str(m.month),
                "income": getattr(m, "income", 0),
                "budgeted": getattr(m, "budgeted", 0),
                "activity": getattr(m, "activity", 0),
                "to_be_budgeted": getattr(m, "to_be_budgeted", 0),
                "categories": [
                    {
                        "id": c.id,
                        "name": c.name,
                        "budgeted": getattr(c, "budgeted", 0),
                        "activity": getattr(c, "activity", 0),
                        "balance": getattr(c, "balance", 0),
                    }
                    for c in (getattr(m, "categories", None) or [])
                ],
            }

    # ------------------------------------------------------------------
    # Connection test
    # ------------------------------------------------------------------

    def test_connection(self) -> str:
        """Validate the token by fetching plans. Returns the first plan name or raises."""
        try:
            plans = self.get_plans()
            if plans:
                return plans[0]["name"]
            raise YNABClientError("No plans found.")
        except Exception as exc:
            raise YNABClientError(f"YNAB connection test failed: {exc}") from exc
