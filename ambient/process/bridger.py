#!/usr/bin/env python3
"""
ambient/process/bridger.py — Context Bridger processing worker.

The highest-value processing step. Connects dots across persona-specific
insights that create cross-domain value no single persona would see.

Examples:
- Financial insight + calendar event = preparation opportunity
- Health/activity gap + free time = coaching moment
- Product news + inventory data = business action
- Grow tent trend + knowledge base match = proactive advice

Runs every hour. Uses Sonnet/Opus-class model for cross-domain reasoning.

Usage:
    python -m ambient.process.bridger
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from ambient.llm import AmbientLLM
from ambient.worker import BaseWorker

logger = logging.getLogger("ambient.process.bridger")

BRIDGER_SYSTEM_PROMPT = """You are the Context Bridger for Kiro, Tim's AI personal assistant. This is the highest-value processing step in Kiro's ambient intelligence layer.

Your job: look at recent insights from different personas and find connections that no single persona would see alone. You connect dots across domains.

Tim's life context:
- Runs a cannabis retail store in BC, Canada
- Grows cannabis at home (indoor tent)
- Uses YNAB for budgeting
- Has an AI system with personas: Jack (grower), Finley (finance), Ops (operations), Sage (product knowledge), Coach (fitness), Chef (nutrition)

Types of bridges to look for:
1. **Preparation bridges**: Financial insight + calendar event → "prep for this meeting"
2. **Opportunity bridges**: Product news + inventory gap → "stock this product"
3. **Warning bridges**: Grow trend + knowledge match → "watch out for this"
4. **Efficiency bridges**: Schedule gap + pending task → "use this time for that"
5. **Health bridges**: Activity gap + free time → coaching opportunity
6. **Financial bridges**: Spending pattern + upcoming obligation → budget warning

For each bridge found, provide:
- summary: Clear, actionable description of the connection
- detail: How the insights connect and what Tim should do
- source_insights: IDs of the insights being connected
- confidence: "high", "medium", or "low"
- priority: 1-10 (bridges that connect multiple domains get -1; time-sensitive get -3)
- tags: relevant topic tags

Only create bridges that add genuine value. If two insights are vaguely related but the connection isn't actionable or insightful, don't bridge them.

Respond as JSON: {"bridges": [...]}. If no meaningful bridges found, return {"bridges": []}."""


class ContextBridgerWorker(BaseWorker):
    """
    Cross-persona connection engine. Finds relationships between
    domain-specific insights that create actionable cross-domain value.
    """

    worker_name = "process_bridger"
    default_interval_seconds = 3600  # 1 hour

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._llm: AmbientLLM | None = None

    def setup(self) -> None:
        """Initialize LLM client."""
        model_routing = self.db.get_config("model_routing", {})
        self._llm = AmbientLLM(model_routing=model_routing if isinstance(model_routing, dict) else {})
        self.audit_log("INFO", "Context Bridger initialized")

    def _group_insights_by_persona(self, insights: List[Dict]) -> Dict[str, List[Dict]]:
        """Group insights by persona for structured analysis."""
        grouped: Dict[str, List[Dict]] = {}
        for insight in insights:
            persona = insight.get("persona") or "kiro"
            if persona not in grouped:
                grouped[persona] = []
            grouped[persona].append({
                "id": insight["id"],
                "type": insight.get("insight_type", ""),
                "summary": insight.get("summary", ""),
                "detail": insight.get("detail", ""),
                "priority": insight.get("priority", 5),
                "tags": insight.get("tags", []),
                "created": str(insight.get("created_at", ""))[:19],
            })
        return grouped

    def process(self) -> None:
        """Look for cross-domain connections between recent insights."""
        # Get all recent unsurfaced insights across all personas
        insights = self.db.get_unsurfaced_insights(max_priority=8, limit=30)

        # Also get recently surfaced insights (last 24h) for context
        recent = self.db.get_recent_insights(hours=24)

        all_insights = {i["id"]: i for i in insights + recent}
        unique_insights = list(all_insights.values())

        if len(unique_insights) < 2:
            logger.debug("Not enough insights for bridging (%d)", len(unique_insights))
            return

        # Check how many different personas are represented
        personas = set(i.get("persona") or "kiro" for i in unique_insights)
        if len(personas) < 2:
            logger.debug("Insights from only %d persona(s), skipping bridger", len(personas))
            return

        # Group for structured prompt
        grouped = self._group_insights_by_persona(unique_insights)

        # Get upcoming calendar events for context
        calendar_events = self.db.get_events_in_window(days=3, source="gcal", processed_only=False)
        calendar_summary = []
        for ce in calendar_events[:10]:
            meta = ce.get("metadata", {})
            calendar_summary.append({
                "event": meta.get("summary", ""),
                "start": meta.get("start", ""),
                "location": meta.get("location", ""),
            })

        user_prompt = f"""Here are the recent insights from Kiro's personas. Find meaningful cross-domain connections.

## Insights by Persona
{json.dumps(grouped, indent=2, default=str)}

## Tim's Upcoming Schedule (next 3 days)
{json.dumps(calendar_summary, indent=2, default=str) if calendar_summary else "No calendar data available"}

## Current Time
{datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}

Look for connections between insights from different personas that create actionable value. Only create bridges where the connection genuinely matters.

Respond as JSON: {{"bridges": [...]}}"""

        try:
            result = self._llm.complete_json(
                task="bridger",
                system_prompt=BRIDGER_SYSTEM_PROMPT,
                user_prompt=user_prompt,
                temperature=0.3,
                max_tokens=2000,
            )
        except Exception as e:
            self.audit_log("ERROR", f"Context bridger LLM call failed: {e}")
            return

        bridges = result.get("bridges", [])
        if not bridges:
            logger.debug("No bridges found")
            return

        created = 0
        for bridge in bridges:
            source_insight_ids = bridge.get("source_insights", [])
            tags = bridge.get("tags", [])

            # Dedup check
            existing = self.db.find_similar_unsurfaced_insight(
                insight_type="bridge",
                tags=tags,
                hours=24,
            )
            if existing:
                logger.debug("Similar bridge already exists, skipping")
                continue

            priority = bridge.get("priority", 4)
            priority = max(1, min(10, priority))

            # Bridges connecting multiple domains get a priority boost
            priority = max(1, priority - 1)

            insight_id = self.db.insert_insight(
                insight_type="bridge",
                summary=bridge.get("summary", ""),
                detail=bridge.get("detail"),
                persona=None,  # Bridges are Kiro-level
                confidence=bridge.get("confidence", "medium"),
                priority=priority,
                related_insight_ids=source_insight_ids,
                tags=tags,
                metadata={"source": "context_bridger", "raw_bridge": bridge},
            )
            created += 1

            if priority <= 2:
                self.audit_log("ALERT", f"High-priority bridge: {bridge.get('summary', '')}", {
                    "insight_id": insight_id,
                    "priority": priority,
                })

        if created > 0:
            self.audit_log("INFO", f"Created {created} bridge insights", {
                "bridges_found": len(bridges),
                "created": created,
            })


def main():
    worker = ContextBridgerWorker()
    worker.run()


if __name__ == "__main__":
    main()
