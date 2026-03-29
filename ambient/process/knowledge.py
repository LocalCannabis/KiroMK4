#!/usr/bin/env python3
"""
ambient/process/knowledge.py — Knowledge Builder processing worker.

Actively searches for new knowledge to enrich Jack's and Sage's knowledge
bases. Runs every 12 hours.

Searches for:
- New grow reports matching Tim's active strains
- Cannabis research papers and industry publications
- Updated BC cannabis regulatory information
- Evaluates finds against existing knowledge base

Usage:
    python -m ambient.process.knowledge
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from ambient.llm import AmbientLLM
from ambient.worker import BaseWorker

logger = logging.getLogger("ambient.process.knowledge")

KNOWLEDGE_EVAL_SYSTEM_PROMPT = """You are the Knowledge Builder for Kiro, Tim's AI personal assistant.

Your job is to evaluate information from various sources and determine:
1. Is this information relevant to Tim's grows, store, or cannabis knowledge?
2. Is it trustworthy and consistent with established science?
3. Does it add new value beyond what's already in the knowledge base?
4. How confident should we be in this information?

Tim grows cannabis indoors in a 2x2 tent using living soil (peat-based, ProMix style).
He relies on scientific approaches (Dr. Bruce Bugbee's research) and practical grower wisdom.

For each piece of information, evaluate:
- relevance: 1-10 (1=highly relevant, 10=irrelevant)
- quality: "high", "medium", "low"
- confidence: "very_high", "high", "medium_high", "medium", "low"
- domain_tags: list of topic tags
- summary: concise summary of the key information
- should_store: boolean — is this worth adding to the knowledge base?
- insight_worthy: boolean — should this be flagged as a notable insight for Tim?

Respond as JSON."""


class KnowledgeBuilderWorker(BaseWorker):
    """
    Background research worker. Searches for new knowledge relevant
    to Tim's grows and store, evaluates quality, and enriches the
    knowledge base.
    """

    worker_name = "process_knowledge"
    default_interval_seconds = 43200  # 12 hours

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._llm: AmbientLLM | None = None
        self._jack_db = None

    def setup(self) -> None:
        """Initialize LLM and Jack's DB for knowledge base access."""
        model_routing = self.db.get_config("model_routing", {})
        self._llm = AmbientLLM(model_routing=model_routing if isinstance(model_routing, dict) else {})

        # Check if knowledge research is enabled
        enabled = self.db.get_config("knowledge_research_enabled", True)
        if not enabled:
            self.audit_log("INFO", "Knowledge research is disabled in config")

        # Load interval from config
        interval_hours = self.db.get_config("knowledge_research_interval_hours", 12)
        if isinstance(interval_hours, (int, float)):
            self._interval = int(interval_hours) * 3600

        self.audit_log("INFO", "Knowledge Builder initialized")

    def _get_active_grows(self) -> List[Dict]:
        """Get active grows from Jack's database."""
        try:
            from jack.config import load_jack_config
            from jack.db import JackDB
            cfg = load_jack_config()
            jack_db = JackDB(cfg)
            grows = jack_db.get_active_grows()
            jack_db.close()
            return grows
        except Exception as e:
            logger.warning("Could not load active grows: %s", e)
            return []

    def _evaluate_feed_content(self, events: List[Dict]) -> List[Dict]:
        """Evaluate recent feed articles for knowledge base worthy content."""
        if not events:
            return []

        # Batch articles for evaluation
        articles = []
        for event in events[:20]:
            meta = event.get("metadata", {})
            content = event.get("raw_content", "")
            articles.append({
                "event_id": event["id"],
                "title": meta.get("title", ""),
                "source": meta.get("feed_name", ""),
                "category": meta.get("feed_category", ""),
                "content_preview": content[:500] if content else "",
                "tags": event.get("tags", []),
            })

        if not articles:
            return []

        user_prompt = f"""Evaluate these recent articles for knowledge base inclusion.

Active strains Tim is growing: {json.dumps([g.get('strain', '') for g in self._get_active_grows()])}

Articles:
{json.dumps(articles, indent=2, default=str)}

For each article, determine if it should be stored in the knowledge base and/or flagged as an insight for Tim.

Respond as JSON: {{"evaluations": [{{"event_id": N, "relevance": N, "quality": "...", "confidence": "...", "domain_tags": [...], "summary": "...", "should_store": bool, "insight_worthy": bool}}]}}"""

        try:
            result = self._llm.complete_json(
                task="knowledge",
                system_prompt=KNOWLEDGE_EVAL_SYSTEM_PROMPT,
                user_prompt=user_prompt,
                temperature=0.2,
                max_tokens=2000,
            )
            return result.get("evaluations", [])
        except Exception as e:
            logger.warning("Knowledge evaluation failed: %s", e)
            return []

    def process(self) -> None:
        """Evaluate recent feed articles and grow-related events for knowledge base enrichment."""
        enabled = self.db.get_config("knowledge_research_enabled", True)
        if not enabled:
            logger.debug("Knowledge research disabled")
            return

        # Get recent feed articles that haven't been evaluated for knowledge
        feed_events = self.db.get_events_in_window(days=1, source="feed", processed_only=True)

        # Filter to those with cultivation or product tags
        relevant_events = []
        for event in feed_events:
            tags = event.get("tags") or []
            tag_str = " ".join(tags).lower()
            if any(kw in tag_str for kw in [
                "cultivation", "grow", "cannabis", "strain", "terpene",
                "harvest", "flower", "veg", "nutrient", "soil", "light",
                "vpd", "dli", "bcldb", "regulatory", "product",
            ]):
                relevant_events.append(event)

        if not relevant_events:
            logger.debug("No relevant feed articles for knowledge evaluation")
            return

        evaluations = self._evaluate_feed_content(relevant_events)

        insights_created = 0
        for evaluation in evaluations:
            if not evaluation.get("insight_worthy"):
                continue

            summary = evaluation.get("summary", "")
            if not summary:
                continue

            # Create a knowledge insight
            self.db.insert_insight(
                insight_type="knowledge",
                summary=summary,
                detail=f"Source evaluation — quality: {evaluation.get('quality')}, confidence: {evaluation.get('confidence')}",
                persona="jack" if any(t in evaluation.get("domain_tags", []) for t in ["grow", "cultivation", "strain"]) else "sage",
                confidence=evaluation.get("confidence", "medium"),
                priority=6,  # Knowledge insights are informational
                source_event_ids=[evaluation.get("event_id")] if evaluation.get("event_id") else [],
                tags=evaluation.get("domain_tags", []),
                metadata={"source": "knowledge_builder", "evaluation": evaluation},
            )
            insights_created += 1

        if insights_created > 0:
            self.audit_log("INFO", f"Created {insights_created} knowledge insights", {
                "articles_evaluated": len(evaluations),
                "insights_created": insights_created,
            })
        else:
            logger.debug("No knowledge insights to create from %d evaluations", len(evaluations))


def main():
    worker = KnowledgeBuilderWorker()
    worker.run()


if __name__ == "__main__":
    main()
