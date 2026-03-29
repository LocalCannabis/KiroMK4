#!/usr/bin/env python3
"""
ambient/process/tagger.py — Event Tagger processing worker.

Picks up unprocessed events from kiro_events, uses a lightweight LLM
(Haiku-class) to extract tags, classify relevance, and assign personas.

This is the first processing step in the pipeline:
    ingest → kiro_events → [TAGGER] → tagged events

Runs every 5 minutes. Uses the cheapest fast model for cost control.

Usage:
    python -m ambient.process.tagger
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List

from ambient.llm import AmbientLLM
from ambient.worker import BaseWorker

logger = logging.getLogger("ambient.process.tagger")

TAGGER_SYSTEM_PROMPT = """You are an event tagger for Kiro, Tim's AI personal assistant. Your job is to analyze raw events from various data streams and extract structured tags.

Tim runs a cannabis retail store in BC, Canada. He also grows cannabis at home (indoor tent grows). He has an AI assistant system with multiple personas:
- Jack: Master grower (cannabis cultivation)
- Finley: Financial advisor (budgets via YNAB)
- Ops: Operations/staff management
- Sage: Cannabis product knowledge
- Coach: Health and fitness
- Chef: Cooking and nutrition
- Doc: Health monitoring
- Kiro: General orchestrator

For each event, extract:
1. **tags**: List of relevant topic tags (e.g., "cannabis", "staff", "finances", "grow-tent", "weather", "product-drop", "bcldb", "health", etc.)
2. **relevance**: 1-10 score (1=critical to Tim, 10=irrelevant noise)
3. **personas**: List of persona names this is relevant to
4. **needs_deeper_analysis**: boolean — true if this event contains complex information that needs pattern detection or bridging
5. **action_items**: List of any action items detected (empty list if none)

Respond in JSON format only."""

TAGGER_USER_TEMPLATE = """Analyze this event:

Source: {source}
Type: {event_type}
Occurred: {occurred_at}
Metadata: {metadata}
Content: {content}

Extract tags, relevance score, relevant personas, whether it needs deeper analysis, and any action items. Respond as JSON:
{{"tags": [...], "relevance": N, "personas": [...], "needs_deeper_analysis": bool, "action_items": [...]}}"""


class EventTaggerWorker(BaseWorker):
    """
    Processes untagged events through a lightweight LLM for
    classification, tagging, and persona assignment.
    """

    worker_name = "process_tagger"
    default_interval_seconds = 300  # 5 minutes

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._llm: AmbientLLM | None = None

    def setup(self) -> None:
        """Initialize the LLM client with model routing from config."""
        model_routing = self.db.get_config("model_routing", {})
        self._llm = AmbientLLM(model_routing=model_routing if isinstance(model_routing, dict) else {})
        self.audit_log("INFO", "Event Tagger initialized")

    def _tag_event(self, event: Dict) -> Dict[str, Any]:
        """Send a single event to the LLM for tagging."""
        content = event.get("raw_content") or ""
        if len(content) > 2000:
            content = content[:2000] + "..."

        metadata_str = json.dumps(event.get("metadata", {}), default=str)
        if len(metadata_str) > 1000:
            metadata_str = metadata_str[:1000] + "..."

        user_prompt = TAGGER_USER_TEMPLATE.format(
            source=event.get("source", ""),
            event_type=event.get("event_type", ""),
            occurred_at=event.get("occurred_at", ""),
            metadata=metadata_str,
            content=content,
        )

        try:
            result = self._llm.complete_json(
                task="tagger",
                system_prompt=TAGGER_SYSTEM_PROMPT,
                user_prompt=user_prompt,
                temperature=0.1,
                max_tokens=500,
            )
            return result
        except Exception as e:
            logger.warning("LLM tagging failed for event %s: %s", event.get("id"), e)
            # Fallback: basic tags from source
            return {
                "tags": [event.get("source", "unknown")],
                "relevance": 5,
                "personas": [],
                "needs_deeper_analysis": False,
                "action_items": [],
            }

    def process(self) -> None:
        """Process all untagged events."""
        events = self.db.get_unprocessed_events(limit=50)
        if not events:
            logger.debug("No unprocessed events")
            return

        tagged_count = 0
        for event in events:
            result = self._tag_event(event)

            # Extract tags
            tags = result.get("tags", [])
            if not isinstance(tags, list):
                tags = [str(tags)]

            # Add source-based tags
            tags.append(event.get("source", ""))
            tags.append(event.get("event_type", ""))

            # Add persona tags
            personas = result.get("personas", [])
            for p in personas:
                tags.append(f"persona:{p}")

            # Store deeper analysis flag and action items in metadata
            if result.get("needs_deeper_analysis") or result.get("action_items"):
                conn = self.db._conn()
                try:
                    with conn.cursor() as cur:
                        import psycopg2.extras
                        cur.execute("""
                            UPDATE kiro_events
                            SET metadata = metadata || %s::jsonb
                            WHERE id = %s
                        """, (
                            json.dumps({
                                "relevance": result.get("relevance", 5),
                                "needs_deeper_analysis": result.get("needs_deeper_analysis", False),
                                "action_items": result.get("action_items", []),
                                "assigned_personas": personas,
                            }),
                            event["id"],
                        ))
                    conn.commit()
                finally:
                    self.db._put(conn)

            # Mark as processed with tags
            self.db.mark_event_processed(event["id"], tags)
            tagged_count += 1

        if tagged_count > 0:
            self.audit_log("INFO", f"Tagged {tagged_count} events", {
                "count": tagged_count,
            })


def main():
    worker = EventTaggerWorker()
    worker.run()


if __name__ == "__main__":
    main()
