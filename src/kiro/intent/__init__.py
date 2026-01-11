"""
Intent Classification Module

Classifies user utterances and routes them to appropriate handlers.
"""

from kiro.intent.router import IntentRouter, Intent, IntentCategory

__all__ = ["IntentRouter", "Intent", "IntentCategory"]
