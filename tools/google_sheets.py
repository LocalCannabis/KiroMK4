"""
tools/google_sheets.py — Google Sheets tools for Kiro (owned by Finley).

Voice actions:
  - create_budget: "Finley, set up a budget for me"
  - add_expense: "Finley, I spent $45 on groceries today"
  - get_budget_summary: "Finley, how am I doing on food this month?"
  - update_budget_item: "Finley, my rent just went up to $1,800"
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from .google_auth import get_google_service

logger = logging.getLogger("kiro")


def _service():
    return get_google_service("sheets", "v4")


# ── Default budget categories ────────────────────────────────────────────

_DEFAULT_CATEGORIES = [
    ("Housing", "Rent, mortgage, insurance"),
    ("Utilities", "Electric, water, internet, phone"),
    ("Food & Groceries", "Grocery store, eating out"),
    ("Transport", "Gas, transit, insurance, maintenance"),
    ("Health", "Medical, dental, prescriptions, gym"),
    ("Entertainment", "Subscriptions, hobbies, going out"),
    ("Savings", "Emergency fund, investments, goals"),
    ("Personal", "Clothing, haircuts, gifts"),
    ("Debt", "Credit cards, loans, payments"),
    ("Misc", "Everything else"),
]


def create_budget(title: str = "") -> str:
    """Create a new budget spreadsheet with default categories and an expenses log sheet."""
    try:
        svc = _service()
        now = datetime.now()
        if not title:
            title = f"Kiro Budget — {now.strftime('%B %Y')}"

        spreadsheet_body = {
            "properties": {"title": title},
            "sheets": [
                {
                    "properties": {"title": "Budget", "index": 0},
                },
                {
                    "properties": {"title": "Expenses", "index": 1},
                },
            ],
        }

        ss = svc.spreadsheets().create(body=spreadsheet_body).execute()
        ss_id = ss["spreadsheetId"]

        # Populate Budget sheet with categories
        budget_rows = [["Category", "Monthly Budget", "Spent", "Remaining", "Notes"]]
        for cat, notes in _DEFAULT_CATEGORIES:
            budget_rows.append([cat, 0, f"=SUMPRODUCT((Expenses!A:A=\"{cat}\")*Expenses!C:C)", f"=B{len(budget_rows)+1}-C{len(budget_rows)+1}", notes])
        budget_rows.append([])
        budget_rows.append(["TOTAL", "=SUM(B2:B11)", "=SUM(C2:C11)", "=SUM(D2:D11)", ""])

        # Populate Expenses header
        expense_header = [["Category", "Description", "Amount", "Date"]]

        svc.spreadsheets().values().batchUpdate(
            spreadsheetId=ss_id,
            body={
                "valueInputOption": "USER_ENTERED",
                "data": [
                    {"range": "Budget!A1:E14", "values": budget_rows},
                    {"range": "Expenses!A1:D1", "values": expense_header},
                ],
            },
        ).execute()

        url = f"https://docs.google.com/spreadsheets/d/{ss_id}"
        logger.info("Budget spreadsheet created: %s", url)
        return f"Budget spreadsheet created: {title}. I've set up {len(_DEFAULT_CATEGORIES)} categories and an expenses log."
    except Exception as e:
        logger.error("Sheets create_budget failed: %s", e)
        return f"Sorry, I couldn't create the budget: {e}"


def add_expense(
    category: str,
    description: str,
    amount: float,
    spreadsheet_id: str = "",
) -> str:
    """Add an expense row to the Expenses sheet of the budget spreadsheet."""
    try:
        svc = _service()
        ss_id = spreadsheet_id or _find_budget_spreadsheet()
        if not ss_id:
            return "I don't see a budget spreadsheet yet. Say 'Finley, set up a budget' first."

        date_str = datetime.now().strftime("%Y-%m-%d")
        row = [[category, description, amount, date_str]]

        svc.spreadsheets().values().append(
            spreadsheetId=ss_id,
            range="Expenses!A:D",
            valueInputOption="USER_ENTERED",
            insertDataOption="INSERT_ROWS",
            body={"values": row},
        ).execute()

        return f"Logged {description}: ${amount:.2f} under {category}."
    except Exception as e:
        logger.error("Sheets add_expense failed: %s", e)
        return f"Sorry, I couldn't log that expense: {e}"


def get_budget_summary(
    category: str = "",
    spreadsheet_id: str = "",
) -> str:
    """Read budget summary — overall or for a specific category."""
    try:
        svc = _service()
        ss_id = spreadsheet_id or _find_budget_spreadsheet()
        if not ss_id:
            return "No budget spreadsheet found. Say 'Finley, set up a budget' first."

        result = svc.spreadsheets().values().get(
            spreadsheetId=ss_id,
            range="Budget!A1:E14",
        ).execute()
        rows = result.get("values", [])
        if len(rows) < 2:
            return "The budget sheet looks empty."

        header = rows[0]
        data_rows = rows[1:]

        if category:
            cat_lower = category.lower()
            for row in data_rows:
                if row and row[0].lower().startswith(cat_lower):
                    budget = row[1] if len(row) > 1 else "0"
                    spent = row[2] if len(row) > 2 else "0"
                    remaining = row[3] if len(row) > 3 else "0"
                    return f"{row[0]}: budgeted ${budget}, spent ${spent}, ${remaining} remaining."
            return f"I don't see a '{category}' category in your budget."

        # Overall summary
        totals_row = None
        for row in reversed(data_rows):
            if row and row[0].upper() == "TOTAL":
                totals_row = row
                break

        if totals_row:
            budget = totals_row[1] if len(totals_row) > 1 else "?"
            spent = totals_row[2] if len(totals_row) > 2 else "?"
            remaining = totals_row[3] if len(totals_row) > 3 else "?"
            return f"Overall: budgeted ${budget}, spent ${spent}, ${remaining} remaining this month."
        return "Budget data looks incomplete — try checking the spreadsheet directly."
    except Exception as e:
        logger.error("Sheets get_budget_summary failed: %s", e)
        return f"Sorry, I couldn't read your budget: {e}"


def update_budget_item(
    category: str,
    monthly_budget: float,
    spreadsheet_id: str = "",
) -> str:
    """Update the monthly budget amount for a category."""
    try:
        svc = _service()
        ss_id = spreadsheet_id or _find_budget_spreadsheet()
        if not ss_id:
            return "No budget spreadsheet found."

        result = svc.spreadsheets().values().get(
            spreadsheetId=ss_id,
            range="Budget!A2:A11",
        ).execute()
        rows = result.get("values", [])

        cat_lower = category.lower()
        for i, row in enumerate(rows):
            if row and row[0].lower().startswith(cat_lower):
                cell = f"Budget!B{i + 2}"
                svc.spreadsheets().values().update(
                    spreadsheetId=ss_id,
                    range=cell,
                    valueInputOption="USER_ENTERED",
                    body={"values": [[monthly_budget]]},
                ).execute()
                return f"Updated {row[0]} budget to ${monthly_budget:.2f} per month."
        return f"I don't see a '{category}' category in your budget."
    except Exception as e:
        logger.error("Sheets update_budget_item failed: %s", e)
        return f"Sorry, I couldn't update that: {e}"


def _find_budget_spreadsheet() -> str:
    """Find the most recent Kiro Budget spreadsheet in Google Drive."""
    try:
        from .google_auth import get_google_service
        drive = get_google_service("drive", "v3")
        result = drive.files().list(
            q="name contains 'Kiro Budget' and mimeType='application/vnd.google-apps.spreadsheet' and trashed=false",
            orderBy="modifiedTime desc",
            pageSize=1,
            fields="files(id, name)",
        ).execute()
        files = result.get("files", [])
        if files:
            logger.info("Found budget spreadsheet: %s (%s)", files[0]["name"], files[0]["id"])
            return files[0]["id"]
        return ""
    except Exception as e:
        logger.warning("Drive search for budget failed: %s", e)
        return ""
