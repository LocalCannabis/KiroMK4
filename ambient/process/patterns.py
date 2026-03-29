#!/usr/bin/env python3
"""
ambient/process/patterns.py — Pattern Detector processing worker.

Analyzes tagged events over a rolling window to detect:
- Frequency patterns: same topic appearing multiple times across sources
- Trend patterns: numeric values trending consistently
- Absence patterns: expected events that didn't happen
- Correlation patterns: cross-source relationships

Runs every 30 minutes. Uses Sonnet-class model for reasoning.

Usage:
    python -m ambient.process.patterns
"""

from __future__ import annotations

import json
import logging
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from ambient.llm import AmbientLLM
from ambient.worker import BaseWorker

logger = logging.getLogger("ambient.process.patterns")

PATTERN_SYSTEM_PROMPT = """You are the pattern detection engine for Kiro, Tim's AI personal assistant.

You analyze events across multiple data streams looking for meaningful patterns, trends, anomalies, and correlations. Tim runs a cannabis retail store in BC, Canada and grows cannabis at home.

Your job is to identify patterns that Tim would want to know about but might not notice himself. Be selective — only flag genuinely meaningful patterns, not noise.

Types of patterns to detect:
1. **Frequency patterns**: Same topic/theme appearing 3+ times across different sources
2. **Trend patterns**: Numeric values (spending, tent readings, etc.) moving consistently in one direction over 3+ data points
3. **Absence patterns**: Expected events that didn't happen (no grow checkin in 48hrs, recurring meeting cancelled)
4. **Anomaly patterns**: Unusual values or events that deviate significantly from normal

For each pattern found, provide:
- type: "pattern", "trend", "anomaly", or "absence"
- summary: Clear, concise description
- detail: Longer explanation with supporting data
- persona: Which persona should own this (jack, finley, ops, sage, coach, etc.) or null for cross-persona
- confidence: "high", "medium", or "low"
- priority: 1-10 (1=critical, 10=trivial) based on these factors:
  - Time sensitivity (-3 if action needed today)
  - Financial impact (-2 if involves significant money)
  - Health/safety (-4 for grow tent emergency or health flag)
  - Recurring pattern (-1 if seen 3+ times)
  - First occurrence (-1 for novel info)
  - Low confidence (+2)
  - Informational only (+2)
- tags: relevant topic tags

Respond as JSON with a "patterns" array. If no patterns found, return {"patterns": []}.
"""


class PatternDetectorWorker(BaseWorker):
    """
    Analyzes processed events over a rolling window to detect
    patterns, trends, and anomalies. Generates kiro_insights.
    """

    worker_name = "process_patterns"
    default_interval_seconds = 1800  # 30 minutes

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._llm: AmbientLLM | None = None

    def setup(self) -> None:
        """Initialize LLM client."""
        model_routing = self.db.get_config("model_routing", {})
        self._llm = AmbientLLM(model_routing=model_routing if isinstance(model_routing, dict) else {})
        self.audit_log("INFO", "Pattern Detector initialized")

    def _pre_analyze_tags(self, events: List[Dict]) -> Dict[str, Any]:
        """Pre-compute tag frequencies and basic stats before LLM analysis."""
        tag_counts: Counter = Counter()
        source_counts: Counter = Counter()
        tags_by_source: Dict[str, Counter] = defaultdict(Counter)
        daily_counts: Dict[str, int] = defaultdict(int)

        for event in events:
            tags = event.get("tags") or []
            source = event.get("source", "")
            occurred = event.get("occurred_at")

            source_counts[source] += 1
            for tag in tags:
                tag_counts[tag] += 1
                tags_by_source[source][tag] += 1

            if occurred:
                day_key = occurred.strftime("%Y-%m-%d") if isinstance(occurred, datetime) else str(occurred)[:10]
                daily_counts[day_key] += 1

        return {
            "total_events": len(events),
            "top_tags": tag_counts.most_common(20),
            "source_distribution": dict(source_counts),
            "frequent_tags": {tag: count for tag, count in tag_counts.items() if count >= 3},
            "daily_counts": dict(sorted(daily_counts.items())),
        }

    def _extract_grow_trends(self, events: List[Dict]) -> List[Dict]:
        """Extract numeric trend data from grow log events."""
        grow_events = [e for e in events if e.get("source") == "grow_log"]
        if len(grow_events) < 3:
            return []

        # Sort by time
        grow_events.sort(key=lambda e: e.get("occurred_at", datetime.min))

        metrics = ["humidity_tent", "temp_canopy_c", "vpd_kpa", "dli_estimate"]
        trends = []

        for metric in metrics:
            values = []
            for e in grow_events:
                meta = e.get("metadata", {})
                val = meta.get(metric)
                if val is not None:
                    values.append({
                        "value": float(val),
                        "date": str(e.get("occurred_at", ""))[:10],
                    })

            if len(values) >= 3:
                trends.append({
                    "metric": metric,
                    "data_points": values[-10:],  # Last 10 readings
                    "latest": values[-1]["value"],
                    "oldest": values[0]["value"],
                    "direction": "up" if values[-1]["value"] > values[0]["value"] else "down",
                })

        return trends

    def _extract_spending_trends(self, events: List[Dict]) -> List[Dict]:
        """Extract spending trend data from YNAB events."""
        ynab_events = [e for e in events if e.get("source") == "ynab" and e.get("event_type") == "transaction"]
        if not ynab_events:
            return []

        # Group by category
        by_category: Dict[str, List] = defaultdict(list)
        for e in ynab_events:
            meta = e.get("metadata", {})
            cat = meta.get("category_name", "Uncategorized")
            amount = meta.get("amount_dollars", 0)
            if amount < 0:  # Only spending (negative amounts)
                by_category[cat].append({
                    "amount": abs(amount),
                    "date": str(e.get("occurred_at", ""))[:10],
                    "payee": meta.get("payee_name", ""),
                })

        trends = []
        for cat, txns in by_category.items():
            if len(txns) >= 2:
                total = sum(t["amount"] for t in txns)
                trends.append({
                    "category": cat,
                    "transaction_count": len(txns),
                    "total_spent": round(total, 2),
                    "transactions": txns[-5:],  # Last 5
                })

        return sorted(trends, key=lambda t: t["total_spent"], reverse=True)[:10]

    def process(self) -> None:
        """Analyze recent events for patterns."""
        window_days = self.db.get_config("pattern_detection_window_days", 7)
        if not isinstance(window_days, int):
            window_days = int(window_days)

        events = self.db.get_events_in_window(days=window_days, processed_only=True)
        if len(events) < 5:
            logger.debug("Not enough events for pattern detection (%d)", len(events))
            return

        # Pre-compute stats
        stats = self._pre_analyze_tags(events)
        grow_trends = self._extract_grow_trends(events)
        spending_trends = self._extract_spending_trends(events)

        # Build context for LLM
        user_prompt = f"""Analyze the following event data from the last {window_days} days for patterns, trends, and anomalies.

## Event Statistics
Total events: {stats['total_events']}
Source distribution: {json.dumps(stats['source_distribution'])}
Daily event counts: {json.dumps(stats['daily_counts'])}

## Frequently Occurring Tags (3+ times)
{json.dumps(stats['frequent_tags'], indent=2)}

## Top Tags
{json.dumps(stats['top_tags'][:15])}

## Grow Tent Trends
{json.dumps(grow_trends, indent=2) if grow_trends else "No grow data available"}

## Spending Trends
{json.dumps(spending_trends, indent=2) if spending_trends else "No spending data available"}

## Recent Notable Events (action items or deeper analysis flagged)
"""
        notable = [
            {
                "source": e.get("source"),
                "type": e.get("event_type"),
                "occurred": str(e.get("occurred_at", ""))[:19],
                "tags": e.get("tags", [])[:5],
                "action_items": e.get("metadata", {}).get("action_items", []),
            }
            for e in events
            if e.get("metadata", {}).get("needs_deeper_analysis")
            or e.get("metadata", {}).get("action_items")
        ][:20]
        user_prompt += json.dumps(notable, indent=2, default=str)

        user_prompt += """

Identify meaningful patterns. Remember:
- Base priority starts at 5, adjust with scoring factors
- Only flag genuinely important patterns (signal over noise)
- Check for absence patterns (things that should have happened but didn't)
- Look for cross-source correlations

Respond as JSON: {"patterns": [...]}"""

        try:
            result = self._llm.complete_json(
                task="patterns",
                system_prompt=PATTERN_SYSTEM_PROMPT,
                user_prompt=user_prompt,
                temperature=0.3,
                max_tokens=2000,
            )
        except Exception as e:
            self.audit_log("ERROR", f"Pattern detection LLM call failed: {e}")
            return

        patterns = result.get("patterns", [])
        if not patterns:
            logger.debug("No patterns detected")
            return

        created = 0
        for pattern in patterns:
            # Dedup check: does a similar insight already exist?
            tags = pattern.get("tags", [])
            insight_type = pattern.get("type", "pattern")
            persona = pattern.get("persona")

            existing = self.db.find_similar_unsurfaced_insight(
                insight_type=insight_type,
                tags=tags,
                persona=persona,
                hours=48,
            )

            if existing:
                # Update existing insight instead of creating duplicate
                logger.debug("Updating existing insight %d instead of creating duplicate", existing["id"])
                continue

            priority = pattern.get("priority", 5)
            priority = max(1, min(10, priority))  # Clamp

            insight_id = self.db.insert_insight(
                insight_type=insight_type,
                summary=pattern.get("summary", ""),
                detail=pattern.get("detail"),
                persona=persona,
                confidence=pattern.get("confidence", "medium"),
                priority=priority,
                tags=tags,
                metadata={"source": "pattern_detector", "raw_pattern": pattern},
            )
            created += 1

            # Check for immediate alert threshold
            if priority <= 2:
                self.audit_log("ALERT", f"High-priority insight created: {pattern.get('summary', '')}", {
                    "insight_id": insight_id,
                    "priority": priority,
                })

        if created > 0:
            self.audit_log("INFO", f"Created {created} pattern insights", {
                "patterns_found": len(patterns),
                "created": created,
            })


def main():
    worker = PatternDetectorWorker()
    worker.run()


if __name__ == "__main__":
    main()
