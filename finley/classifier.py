"""
finley/classifier.py — Intelligent transaction classifier & pattern detector.

Scans the local SQLite cache of YNAB transactions and:
  1. Auto-classifies payees into spending categories
  2. Detects recurring bills vs one-off purchases
  3. Identifies income sources (paycheck, side gig, etc.)
  4. Tags merchants by type (grocery, restaurant, subscription, etc.)

The output feeds into the financial profile and gives Finley deep
understanding of Tim's money flow — even when YNAB categories are
blank ("Uncategorized").
"""

from __future__ import annotations

import logging
import re
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from .db import FinleyDB, format_currency, milliunits_to_dollars

logger = logging.getLogger("kiro.finley.classifier")


# ---------------------------------------------------------------------------
# Payee → category mapping rules
# ---------------------------------------------------------------------------
# Each rule: (compiled regex on payee_name, category, subcategory, merchant_type)
# Order matters — first match wins.

_PAYEE_RULES: List[Tuple[re.Pattern, str, str, str]] = [
    # ── Income ──────────────────────────────────────────────────────────
    (re.compile(r"direct\s+deposit|payroll|pay\s+hub", re.I),
     "Income", "employment", "employer"),
    (re.compile(r"deposit\s+on.the.go|deposit\s+otg|atm\s+deposit|mobile\s+deposit", re.I),
     "Income", "deposit", "bank"),
    (re.compile(r"e-?transfer\s*(received|from|in)", re.I),
     "Income", "transfer_in", "person"),
    (re.compile(r"government|cra|canada\s+revenue|ei\s+deposit|gst\s+credit|trillium|cerb|crb", re.I),
     "Income", "government", "government"),
    (re.compile(r"refund|rebate|cashback", re.I),
     "Income", "refund", "refund"),

    # ── Housing ─────────────────────────────────────────────────────────
    (re.compile(r"rent|landlord|property\s+mgmt|strata|condo\s+fee", re.I),
     "Housing", "rent", "landlord"),
    (re.compile(r"bc\s+hydro|hydro|fortis|electricity|enmax|power\s+bill", re.I),
     "Housing", "utilities", "utility"),
    (re.compile(r"telus|shaw|rogers|freedom\s+mobile|koodo|fido|bell\s+(mobility|canada)|virgin\s+(plus|mobile)|chatr|public\s+mobile|lucky\s+mobile", re.I),
     "Housing", "phone_internet", "telecom"),
    (re.compile(r"netflix|disney\+?|crave|apple\s+tv|paramount|amazon\s+prime\s+video|hulu|spotify|youtube\s+(premium|music)", re.I),
     "Housing", "streaming", "streaming"),

    # ── Transportation ──────────────────────────────────────────────────
    (re.compile(r"icbc|insurance\s+corp|autoplan|auto\s+insurance|mpi|sgI", re.I),
     "Transportation", "insurance", "insurance"),
    (re.compile(r"esso|shell|petro|chevron|husky|gas\s+bar|pioneer|co-?op\s+gas|ultra?mar|canadian\s+tire\s+gas", re.I),
     "Transportation", "fuel", "gas_station"),
    (re.compile(r"translink|compass|bus\s+pass|skytrain|transit|uber|lyft|evo\s+car|modo|poparide", re.I),
     "Transportation", "transit", "transit"),
    (re.compile(r"parking|impark|easypark|park\s+indigo|diamond\s+parking", re.I),
     "Transportation", "parking", "parking"),

    # ── Food & Drink ────────────────────────────────────────────────────
    (re.compile(r"superstore|save[\s-]on|safeway|no\s+frills|loblaws|costco|walmart\s+super|t&t|h[\s-]mart|whole\s+foods|buy[\s-]?low|fresh\s+st|iga|metro|farm\s+boy|sobeys|tama\s+supermarket", re.I),
     "Food", "groceries", "grocery_store"),
    (re.compile(r"doordash|skip\s*the\s*dishes|uber\s+eats|fantuan|instacart", re.I),
     "Food", "delivery", "delivery_app"),
    (re.compile(r"starbucks|tim\s+horton|tims|mcdonald|subway|a&w|wendy|burger\s+king|popeyes|kfc|pizza\s+pizza|domino|freshslice|little\s+caesars|panago|pizza\s+hut|chicko\s+chicken", re.I),
     "Food", "fast_food", "fast_food"),
    (re.compile(r"caf[eé]|coffee|bean|brew|blenz|jj\s+bean|elysian|matchstick|prado|revolver|timbertrain|49th\s+parallel", re.I),
     "Food", "coffee", "cafe"),
    (re.compile(r"pub|bar|grill|tavern|taphouse|brewery|brewpub|tap\s*house|lounge|foy.s|irish\s+pub|cactus\s+club|earls|joeys|brown.s|white\s+spot|boston\s+pizza|moxie|keg|milestone|bells\s+&\s+whistles", re.I),
     "Food", "dining", "restaurant"),
    (re.compile(r"restaurant|bistro|sushi|ramen|pho|thai|indian|chinese|korean|mexican|taco|burrito|diner|eatery|kitchen|sophie.s\s+cosmic", re.I),
     "Food", "dining", "restaurant"),
    (re.compile(r"liquor|beer\s+wine|bc\s+liquor|cold\s+beer|wine\s+store|west\s+coast\s+liquor", re.I),
     "Food", "alcohol", "liquor_store"),

    # ── Shopping ────────────────────────────────────────────────────────
    (re.compile(r"amazon|amzn", re.I),
     "Shopping", "online", "ecommerce"),
    (re.compile(r"walmart(?!\s+super)|canadian\s+tire|home\s+depot|rona|lowe", re.I),
     "Shopping", "general", "department_store"),
    (re.compile(r"dollarama|dollar\s+tree|dollar\s+store|giant\s+tiger", re.I),
     "Shopping", "general", "dollar_store"),
    (re.compile(r"winners|marshalls|value\s+village|salvation\s+army|thrift|goodwill", re.I),
     "Shopping", "clothing", "thrift"),
    (re.compile(r"apple\b(?!\s+tv)|best\s+buy|london\s+drugs|staples|the\s+source|memory\s+express", re.I),
     "Shopping", "electronics", "electronics"),

    # ── Health & Fitness ────────────────────────────────────────────────
    (re.compile(r"ymca|ywca|gym|fitness|goodlife|anytime\s+fitness|planet\s+fitness|steve\s+nash|equinox|crossfit", re.I),
     "Health", "fitness", "gym"),
    (re.compile(r"pharmacy|shoppers|rexall|london\s+drugs\s+rx|pharmasave|prescription", re.I),
     "Health", "pharmacy", "pharmacy"),
    (re.compile(r"dentist|dental|orthodont|hygienist", re.I),
     "Health", "dental", "dentist"),
    (re.compile(r"doctor|clinic|walk[\s-]in|medical|lab|bloodwork|xray", re.I),
     "Health", "medical", "medical"),

    # ── Cannabis ────────────────────────────────────────────────────────
    (re.compile(r"cannabis|dispensary|bccs|dutch\s+love|hobo|muse|city\s+cannabis|local\s+cannabis", re.I),
     "Cannabis", "purchase", "dispensary"),

    # ── Entertainment ───────────────────────────────────────────────────
    (re.compile(r"steam|playstation|xbox|nintendo|epic\s+games|riot|blizzard", re.I),
     "Entertainment", "gaming", "gaming"),
    (re.compile(r"cineplex|landmark|movie|theatre|theater|imax", re.I),
     "Entertainment", "movies", "cinema"),
    (re.compile(r"audible|kindle|kobo|chapters|indigo|book", re.I),
     "Entertainment", "books_audio", "media"),

    # ── Subscriptions & Services ────────────────────────────────────────
    (re.compile(r"apple\s+(com|services|one|icloud|music)|itunes", re.I),
     "Subscriptions", "apple", "digital"),
    (re.compile(r"google\s+(storage|one|play|cloud)|youtube\s+premium", re.I),
     "Subscriptions", "google", "digital"),
    (re.compile(r"adobe|microsoft\s+365|dropbox|icloud|1password|nordvpn|express\s*vpn", re.I),
     "Subscriptions", "software", "digital"),
    (re.compile(r"patreon|onlyfans|substack|medium|twitch\s+sub", re.I),
     "Subscriptions", "creator", "digital"),
    (re.compile(r"spotify|apple\s+music|tidal|deezer|soundcloud", re.I),
     "Subscriptions", "music", "digital"),

    # ── Debt & Lending ──────────────────────────────────────────────────
    (re.compile(r"lenddirect|lend\s+direct|money\s+mart|cash\s+money|payday|easy\s+financial|fairstone|spring\s+financial|goeasy|cash\s+store", re.I),
     "Debt", "lending", "lender"),
    (re.compile(r"credit\s+card\s+payment|visa\s+payment|mastercard\s+payment|amex\s+payment", re.I),
     "Debt", "cc_payment", "credit_card"),

    # ── Pets ────────────────────────────────────────────────────────────
    (re.compile(r"pet|vet|veterinary|petsmart|petcetera|bosley|global\s+pet", re.I),
     "Pets", "pet_care", "pet_store"),

    # ── Personal care ───────────────────────────────────────────────────
    (re.compile(r"barber|haircut|salon|spa|massage|nail|great\s+clips|supercuts", re.I),
     "Personal Care", "grooming", "grooming"),
]


def classify_payee(payee_name: str) -> Dict[str, str]:
    """
    Classify a single payee into category/subcategory/merchant_type.

    Returns:
        {"category": "Food", "subcategory": "groceries", "merchant_type": "grocery_store"}
    or  {"category": "Unknown", "subcategory": "unknown", "merchant_type": "unknown"}
    """
    if not payee_name or payee_name.strip().lower() in ("starting balance", "manual balance adjustment"):
        return {"category": "System", "subcategory": "starting_balance", "merchant_type": "system"}

    for pattern, category, subcategory, merchant_type in _PAYEE_RULES:
        if pattern.search(payee_name):
            return {
                "category": category,
                "subcategory": subcategory,
                "merchant_type": merchant_type,
            }

    return {"category": "Unknown", "subcategory": "unknown", "merchant_type": "unknown"}


# ---------------------------------------------------------------------------
# Recurring detection
# ---------------------------------------------------------------------------

def detect_recurring(
    transactions: List[Dict[str, Any]],
    min_occurrences: int = 2,
    max_interval_days: int = 45,
) -> List[Dict[str, Any]]:
    """
    Identify recurring charges by grouping transactions by payee and
    checking for regular intervals.

    Returns a list of dicts describing each recurring pattern found.
    """
    # Group by payee
    by_payee: Dict[str, List[Dict]] = defaultdict(list)
    for t in transactions:
        pn = t.get("payee_name", "") or ""
        if pn and pn != "Starting Balance":
            by_payee[pn].append(t)

    recurring = []
    for payee, txns in by_payee.items():
        if len(txns) < min_occurrences:
            continue

        # Sort by date
        txns.sort(key=lambda x: x["date"])
        dates = [datetime.strptime(t["date"], "%Y-%m-%d") if isinstance(t["date"], str) else datetime.combine(t["date"], datetime.min.time()) for t in txns]
        amounts = [t["amount"] for t in txns]

        # Calculate intervals between consecutive transactions
        intervals = [(dates[i + 1] - dates[i]).days for i in range(len(dates) - 1)]

        if not intervals:
            continue

        avg_interval = sum(intervals) / len(intervals)
        avg_amount = sum(amounts) / len(amounts)

        # Determine frequency
        frequency = _classify_frequency(avg_interval)
        if frequency is None:
            continue

        # Amount consistency — std dev relative to mean
        if len(amounts) > 1 and avg_amount != 0:
            variance = sum((a - avg_amount) ** 2 for a in amounts) / len(amounts)
            std_dev_pct = (variance ** 0.5) / abs(avg_amount) * 100 if avg_amount else 0
            amount_consistent = std_dev_pct < 30  # within 30% std dev
        else:
            amount_consistent = True

        # Classify as recurring if intervals are somewhat regular
        interval_variance = sum((i - avg_interval) ** 2 for i in intervals) / len(intervals) if intervals else 0
        interval_std = interval_variance ** 0.5

        is_recurring = (
            avg_interval <= max_interval_days
            and interval_std < avg_interval * 0.5  # intervals within 50% of mean
        )

        if is_recurring:
            classification = classify_payee(payee)
            recurring.append({
                "payee": payee,
                "frequency": frequency,
                "avg_amount": int(avg_amount),
                "avg_amount_formatted": format_currency(int(avg_amount)),
                "occurrences": len(txns),
                "avg_interval_days": round(avg_interval, 1),
                "amount_consistent": amount_consistent,
                "first_seen": txns[0]["date"],
                "last_seen": txns[-1]["date"],
                "next_expected": _estimate_next(dates[-1], avg_interval),
                "category": classification["category"],
                "subcategory": classification["subcategory"],
                "is_bill": classification["category"] in (
                    "Housing", "Transportation", "Debt", "Health",
                    "Subscriptions",
                ),
            })

    # Sort by absolute amount (biggest bills first)
    recurring.sort(key=lambda r: abs(r["avg_amount"]), reverse=True)
    return recurring


def _classify_frequency(avg_interval: float) -> Optional[str]:
    """Map an average interval (days) to a human-readable frequency."""
    if avg_interval <= 1.5:
        return "daily"
    elif avg_interval <= 5:
        return "every few days"
    elif avg_interval <= 9:
        return "weekly"
    elif avg_interval <= 18:
        return "bi-weekly"
    elif avg_interval <= 35:
        return "monthly"
    elif avg_interval <= 100:
        return "quarterly"
    elif avg_interval <= 200:
        return "semi-annually"
    elif avg_interval <= 400:
        return "annually"
    return None


def _estimate_next(last_date: datetime, avg_interval: float) -> str:
    """Estimate the next occurrence date."""
    return (last_date + timedelta(days=int(avg_interval))).strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# Income source identification
# ---------------------------------------------------------------------------

def identify_income_sources(
    transactions: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """
    Scan all positive (inflow) transactions and classify income sources.

    Returns a list of income sources with amount, frequency, and type.
    """
    inflows = [
        t for t in transactions
        if t["amount"] > 0
        and (t.get("payee_name") or "") != "Starting Balance"
        and not t.get("transfer_account_id")  # exclude transfers
    ]

    if not inflows:
        return []

    # Group by payee
    by_payee: Dict[str, List[Dict]] = defaultdict(list)
    for t in inflows:
        by_payee[t.get("payee_name", "Unknown")].append(t)

    sources = []
    for payee, txns in by_payee.items():
        txns.sort(key=lambda x: x["date"])
        amounts = [t["amount"] for t in txns]
        total = sum(amounts)
        avg_amount = total // len(amounts)

        # Classify the income type
        income_type = _classify_income_type(payee)

        # Detect frequency if enough data
        frequency = "unknown"
        if len(txns) >= 2:
            dates = [datetime.strptime(t["date"], "%Y-%m-%d") if isinstance(t["date"], str) else datetime.combine(t["date"], datetime.min.time()) for t in txns]
            intervals = [(dates[i + 1] - dates[i]).days for i in range(len(dates) - 1)]
            avg_interval = sum(intervals) / len(intervals)
            frequency = _classify_frequency(avg_interval) or "irregular"

        sources.append({
            "payee": payee,
            "income_type": income_type,
            "frequency": frequency if len(txns) >= 2 else "single",
            "occurrences": len(txns),
            "avg_amount": avg_amount,
            "avg_amount_formatted": format_currency(avg_amount),
            "total": total,
            "total_formatted": format_currency(total),
            "first_seen": txns[0]["date"],
            "last_seen": txns[-1]["date"],
        })

    # Sort by total descending
    sources.sort(key=lambda s: s["total"], reverse=True)
    return sources


def _classify_income_type(payee: str) -> str:
    """Classify an income source by payee name."""
    payee_lower = (payee or "").lower()

    if any(kw in payee_lower for kw in ("direct deposit", "payroll", "pay hub", "deposit hub")):
        return "employment"
    elif any(kw in payee_lower for kw in ("e-transfer", "etransfer", "interac")):
        return "transfer_in"
    elif any(kw in payee_lower for kw in ("cra", "canada revenue", "government", "gst credit", "trillium", "cerb", "crb", "ei deposit")):
        return "government"
    elif any(kw in payee_lower for kw in ("refund", "rebate", "cashback", "return")):
        return "refund"
    elif any(kw in payee_lower for kw in ("dividend", "interest")):
        return "investment"
    else:
        return "other"


# ---------------------------------------------------------------------------
# Full transaction classification pass
# ---------------------------------------------------------------------------

def classify_all_transactions(db: FinleyDB) -> Dict[str, Any]:
    """
    Run the full classifier over every transaction in the cache.

    Returns a comprehensive breakdown:
      - classified_payees: {payee: classification_dict}
      - category_totals:   {category: {total, count}}
      - unknown_payees:    list of payees we couldn't classify
      - stats:             {total_txns, classified_pct, etc.}
    """
    all_txns = db.get_transactions()

    classified_payees: Dict[str, Dict] = {}
    category_totals: Dict[str, Dict] = defaultdict(lambda: {"total": 0, "count": 0})
    unknown_payees: List[str] = []

    for txn in all_txns:
        payee = txn.get("payee_name") or "Unknown"
        if payee not in classified_payees:
            classified_payees[payee] = classify_payee(payee)

        cls = classified_payees[payee]
        cat = cls["category"]
        category_totals[cat]["total"] += txn["amount"]
        category_totals[cat]["count"] += 1

        if cat == "Unknown":
            if payee not in unknown_payees:
                unknown_payees.append(payee)

    total = len(all_txns)
    classified = sum(1 for t in all_txns
                     if classified_payees.get(t.get("payee_name", ""), {}).get("category") != "Unknown")

    return {
        "classified_payees": classified_payees,
        "category_totals": dict(category_totals),
        "unknown_payees": unknown_payees,
        "stats": {
            "total_transactions": total,
            "classified_count": classified,
            "classified_pct": round(classified / total * 100, 1) if total else 0,
            "unknown_count": total - classified,
            "unique_payees": len(classified_payees),
        },
    }


def build_spending_snapshot(db: FinleyDB) -> Dict[str, Any]:
    """
    Build a comprehensive spending snapshot from all transactions.

    This combines classification, recurring detection, and income
    identification into a single report suitable for profile building.
    """
    all_txns = db.get_transactions()
    classification = classify_all_transactions(db)
    recurring = detect_recurring(all_txns)
    income_sources = identify_income_sources(all_txns)

    # Spending by our classified categories (not YNAB categories)
    outflows = [t for t in all_txns if t["amount"] < 0
                and (t.get("payee_name") or "") != "Starting Balance"]
    total_spending = sum(t["amount"] for t in outflows)
    total_income = sum(t["amount"] for t in all_txns
                       if t["amount"] > 0
                       and (t.get("payee_name") or "") != "Starting Balance"
                       and not t.get("transfer_account_id"))

    # Per-category spending using our classifier
    cat_spending: Dict[str, int] = defaultdict(int)
    for t in outflows:
        payee = t.get("payee_name") or "Unknown"
        cls = classification["classified_payees"].get(payee, {})
        cat = cls.get("category", "Unknown")
        cat_spending[cat] += t["amount"]  # already negative

    # Date range
    dates = [t["date"] for t in all_txns if t.get("date")]
    date_range = (min(dates), max(dates)) if dates else (None, None)

    # Monthly bills estimate (sum of recurring outflows)
    monthly_bills = sum(
        abs(r["avg_amount"])
        for r in recurring
        if r["is_bill"] and r["avg_amount"] < 0
    )

    return {
        "total_transactions": len(all_txns),
        "date_range": {"from": date_range[0], "to": date_range[1]},
        "total_spending": total_spending,
        "total_spending_formatted": format_currency(total_spending),
        "total_income": total_income,
        "total_income_formatted": format_currency(total_income),
        "net_flow": total_income + total_spending,
        "net_flow_formatted": format_currency(total_income + total_spending),
        "category_spending": {
            cat: {"total": amt, "total_formatted": format_currency(amt)}
            for cat, amt in sorted(cat_spending.items(), key=lambda x: x[1])
        },
        "recurring_bills": recurring,
        "monthly_bills_estimate": monthly_bills,
        "monthly_bills_formatted": format_currency(-monthly_bills),
        "income_sources": income_sources,
        "classification_stats": classification["stats"],
        "unknown_payees": classification["unknown_payees"],
    }
