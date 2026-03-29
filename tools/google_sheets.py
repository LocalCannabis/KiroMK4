"""
tools/google_sheets.py — Google Sheets tools for Kiro.

Generic spreadsheet functions usable by any persona, plus a YNAB → Sheet
export that Finley can trigger on demand.

Voice actions:
  - read_sheet_range: "Read cells A1 to D10 from the inventory sheet"
  - write_sheet_cells: "Update cell B2 in the tracker to 'Done'"
  - append_sheet_row: "Add a row to the workout log"
  - create_spreadsheet: "Create a new spreadsheet called Project Timeline"
  - export_ynab_summary: "Finley, export my budget to a spreadsheet"
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from .google_auth import get_google_service

logger = logging.getLogger("kiro")


def _service():
    return get_google_service("sheets", "v4")


# ═══════════════════════════════════════════════════════════════════════════
# Generic Spreadsheet Functions — usable by any persona
# ═══════════════════════════════════════════════════════════════════════════

def _drive_service():
    from .google_auth import get_google_service
    return get_google_service("drive", "v3")


def _find_spreadsheet(name: str) -> str:
    """Find a Google Sheet by name. Returns the spreadsheet ID or empty string."""
    try:
        drive = _drive_service()
        safe_name = name.replace("'", "\\'")
        result = drive.files().list(
            q=f"name='{safe_name}' and mimeType='application/vnd.google-apps.spreadsheet' and trashed=false",
            orderBy="modifiedTime desc",
            pageSize=1,
            fields="files(id, name)",
        ).execute()
        files = result.get("files", [])
        if files:
            return files[0]["id"]
        return ""
    except Exception as e:
        logger.warning("Drive search for sheet '%s' failed: %s", name, e)
        return ""


def read_sheet_range(
    spreadsheet_name: str,
    range_notation: str = "A1:Z20",
    sheet_tab: str = "",
) -> str:
    """Read a range of cells from any Google Sheet (found by name).
    range_notation: A1 notation like 'A1:D10'. sheet_tab: optional tab/sheet name."""
    try:
        sheet_id = _find_spreadsheet(spreadsheet_name)
        if not sheet_id:
            return f"I couldn't find a spreadsheet called '{spreadsheet_name}'."

        svc = _service()
        full_range = f"'{sheet_tab}'!{range_notation}" if sheet_tab else range_notation

        result = svc.spreadsheets().values().get(
            spreadsheetId=sheet_id,
            range=full_range,
        ).execute()

        rows = result.get("values", [])
        if not rows:
            return f"No data found in {range_notation}" + (f" on tab '{sheet_tab}'" if sheet_tab else "") + "."

        # Format as spoken-friendly table
        lines = []
        for i, row in enumerate(rows):
            cells = ", ".join(str(c) for c in row)
            lines.append(f"Row {i + 1}: {cells}")

        # Truncate if too many rows
        if len(lines) > 15:
            lines = lines[:15]
            lines.append(f"... and {len(rows) - 15} more rows.")

        return f"From {spreadsheet_name}: " + ". ".join(lines) + "."
    except Exception as e:
        logger.error("Read sheet range failed: %s", e)
        return f"Sorry, I couldn't read that spreadsheet: {e}"


def write_sheet_cells(
    spreadsheet_name: str,
    range_notation: str,
    values: str,
    sheet_tab: str = "",
) -> str:
    """Write values to cells in a Google Sheet. 
    values: pipe-separated rows, comma-separated cells. E.g. 'A,B,C|1,2,3' for 2 rows.
    range_notation: starting cell or range like 'A1' or 'B5:D5'."""
    try:
        sheet_id = _find_spreadsheet(spreadsheet_name)
        if not sheet_id:
            return f"I couldn't find a spreadsheet called '{spreadsheet_name}'."

        svc = _service()
        full_range = f"'{sheet_tab}'!{range_notation}" if sheet_tab else range_notation

        # Parse the values string into a 2D array
        rows = []
        for row_str in values.split("|"):
            rows.append([cell.strip() for cell in row_str.split(",")])

        svc.spreadsheets().values().update(
            spreadsheetId=sheet_id,
            range=full_range,
            valueInputOption="USER_ENTERED",
            body={"values": rows},
        ).execute()

        cell_count = sum(len(r) for r in rows)
        return f"Updated {cell_count} cell{'s' if cell_count != 1 else ''} in {spreadsheet_name} at {range_notation}."
    except Exception as e:
        logger.error("Write sheet cells failed: %s", e)
        return f"Sorry, I couldn't write to that spreadsheet: {e}"


def append_sheet_row(
    spreadsheet_name: str,
    values: str,
    sheet_tab: str = "",
) -> str:
    """Append a row to the bottom of a Google Sheet.
    values: comma-separated cells, e.g. '2025-07-15, Workout, 45 min, Legs'."""
    try:
        sheet_id = _find_spreadsheet(spreadsheet_name)
        if not sheet_id:
            return f"I couldn't find a spreadsheet called '{spreadsheet_name}'."

        svc = _service()
        full_range = f"'{sheet_tab}'!A:Z" if sheet_tab else "A:Z"

        row = [[cell.strip() for cell in values.split(",")]]

        svc.spreadsheets().values().append(
            spreadsheetId=sheet_id,
            range=full_range,
            valueInputOption="USER_ENTERED",
            insertDataOption="INSERT_ROWS",
            body={"values": row},
        ).execute()

        return f"Row added to {spreadsheet_name}" + (f" on tab '{sheet_tab}'" if sheet_tab else "") + "."
    except Exception as e:
        logger.error("Append sheet row failed: %s", e)
        return f"Sorry, I couldn't append to that spreadsheet: {e}"


def create_spreadsheet(title: str, headers: str = "") -> str:
    """Create a new Google Spreadsheet with optional header row.
    headers: comma-separated column names, e.g. 'Date, Category, Amount, Notes'."""
    try:
        svc = _service()
        spreadsheet = svc.spreadsheets().create(
            body={"properties": {"title": title}},
        ).execute()

        sheet_id = spreadsheet["spreadsheetId"]

        if headers:
            header_row = [[h.strip() for h in headers.split(",")]]
            svc.spreadsheets().values().update(
                spreadsheetId=sheet_id,
                range="A1",
                valueInputOption="USER_ENTERED",
                body={"values": header_row},
            ).execute()

        logger.info("Spreadsheet created: %s (%s)", title, sheet_id)
        return f"Spreadsheet created: {title}." + (f" Headers: {headers}." if headers else "")
    except Exception as e:
        logger.error("Create spreadsheet failed: %s", e)
        return f"Sorry, I couldn't create that spreadsheet: {e}"


# ═══════════════════════════════════════════════════════════════════════════
# YNAB → Sheet Export — single source of truth remains YNAB
# ═══════════════════════════════════════════════════════════════════════════

def export_ynab_summary(sheet_name: str = "Kiro Financial Summary") -> str:
    """Pull current data from YNAB (via Finley's local DB) and write a clean
    snapshot to a Google Sheet.  Creates the sheet if it doesn't exist, or
    overwrites the existing one.

    Tabs written:
      - Accounts: name, type, balance, cleared
      - Budget: category group, category, budgeted, activity, balance
      - Recent Transactions: date, payee, category, amount, memo (last 30)
    """
    try:
        # Lazy-import Finley DB to avoid circular deps and hard coupling
        from finley import load_config, FinleyDB, format_currency

        cfg = load_config()
        db = FinleyDB(cfg)

        # ---------- Gather data from local YNAB cache ----------
        accounts = db.get_accounts()
        categories = db.get_categories()
        transactions = db.get_transactions(limit=30)

        # ---------- Find or create the spreadsheet ----------
        svc = _service()
        sheet_id = _find_spreadsheet(sheet_name)

        if not sheet_id:
            spreadsheet = svc.spreadsheets().create(
                body={"properties": {"title": sheet_name}},
            ).execute()
            sheet_id = spreadsheet["spreadsheetId"]
            logger.info("Created export sheet: %s (%s)", sheet_name, sheet_id)
        else:
            # Clear existing data before writing fresh snapshot
            try:
                svc.spreadsheets().values().clear(
                    spreadsheetId=sheet_id, range="Accounts!A:Z"
                ).execute()
                svc.spreadsheets().values().clear(
                    spreadsheetId=sheet_id, range="Budget!A:Z"
                ).execute()
                svc.spreadsheets().values().clear(
                    spreadsheetId=sheet_id, range="Transactions!A:Z"
                ).execute()
            except Exception:
                pass  # Tabs may not exist yet

        # ---------- Ensure tabs exist ----------
        _ensure_tabs(svc, sheet_id, ["Accounts", "Budget", "Transactions"])

        now_str = datetime.now().strftime("%Y-%m-%d %I:%M %p")

        # ---------- Accounts tab ----------
        acct_rows = [["Account", "Type", "Balance", "Cleared Balance", f"Exported {now_str}"]]
        for a in accounts:
            acct_rows.append([
                a.get("name", ""),
                a.get("type", ""),
                format_currency(a.get("balance", 0)),
                format_currency(a.get("cleared_balance", 0)),
            ])

        svc.spreadsheets().values().update(
            spreadsheetId=sheet_id, range="Accounts!A1",
            valueInputOption="USER_ENTERED", body={"values": acct_rows},
        ).execute()

        # ---------- Budget tab ----------
        budget_rows = [["Group", "Category", "Budgeted", "Spent", "Available"]]
        for c in categories:
            budget_rows.append([
                c.get("category_group_name", ""),
                c.get("name", ""),
                format_currency(c.get("budgeted", 0)),
                format_currency(c.get("activity", 0)),
                format_currency(c.get("balance", 0)),
            ])

        svc.spreadsheets().values().update(
            spreadsheetId=sheet_id, range="Budget!A1",
            valueInputOption="USER_ENTERED", body={"values": budget_rows},
        ).execute()

        # ---------- Transactions tab ----------
        txn_rows = [["Date", "Payee", "Category", "Amount", "Memo"]]
        for t in transactions:
            txn_rows.append([
                t.get("date", ""),
                t.get("payee_name", "") or "",
                t.get("category_name", "") or "",
                format_currency(t.get("amount", 0)),
                t.get("memo", "") or "",
            ])

        svc.spreadsheets().values().update(
            spreadsheetId=sheet_id, range="Transactions!A1",
            valueInputOption="USER_ENTERED", body={"values": txn_rows},
        ).execute()

        return (
            f"YNAB snapshot exported to '{sheet_name}'. "
            f"{len(accounts)} accounts, {len(categories)} categories, "
            f"{len(transactions)} recent transactions. "
            f"Exported at {now_str}."
        )
    except ImportError:
        return "Finley YNAB module isn't available — can't export financial data."
    except Exception as e:
        logger.error("YNAB export failed: %s", e)
        return f"Sorry, I couldn't export the YNAB summary: {e}"


def _ensure_tabs(svc, spreadsheet_id: str, tab_names: List[str]) -> None:
    """Create any missing tabs in the spreadsheet."""
    try:
        meta = svc.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
        existing = {s["properties"]["title"] for s in meta.get("sheets", [])}
        requests = []
        for name in tab_names:
            if name not in existing:
                requests.append({"addSheet": {"properties": {"title": name}}})
        if requests:
            svc.spreadsheets().batchUpdate(
                spreadsheetId=spreadsheet_id,
                body={"requests": requests},
            ).execute()
    except Exception as e:
        logger.warning("Tab creation issue: %s", e)
