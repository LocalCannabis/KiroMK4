"""
Capture Pipeline

Detects task/reminder intent in speech and extracts entities.
Uses regex patterns first, falls back to LLM for ambiguous cases.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Optional, Tuple

from kiro.utils.logging import get_logger

logger = get_logger(__name__)


class CaptureIntent(Enum):
    """Types of capture intents."""
    TASK = "task"
    REMINDER = "reminder"
    QUERY_TASKS = "query_tasks"
    QUERY_TODAY = "query_today"
    QUERY_PROJECT = "query_project"
    COMPLETE_TASK = "complete_task"
    UNKNOWN = "unknown"


@dataclass
class ParsedCapture:
    """Result of parsing an utterance."""
    intent: CaptureIntent
    confidence: float
    
    # For tasks
    task_title: Optional[str] = None
    task_priority: Optional[str] = None
    
    # For reminders  
    reminder_message: Optional[str] = None
    trigger_time: Optional[datetime] = None
    
    # For queries
    project_name: Optional[str] = None
    task_reference: Optional[str] = None
    
    # Raw extraction
    entities: dict = None
    
    def __post_init__(self):
        if self.entities is None:
            self.entities = {}


class CapturePipeline:
    """
    Pipeline for capturing tasks and reminders from speech.
    
    Uses pattern matching for common phrases and time parsing.
    """

    # Patterns that indicate task creation intent
    TASK_PATTERNS = [
        (r"(?:i )?need to (.+)", 0.85),
        (r"add (.+) to (?:my )?(?:list|tasks?|todo)", 0.90),
        (r"(?:i )?have to (.+)", 0.80),
        (r"(?:i )?(?:gotta|got to) (.+)", 0.80),
        (r"(?:i )?should (.+)", 0.70),
        (r"(?:please )?(?:create|make|add) (?:a )?task (?:to |for )?(.+)", 0.95),
        (r"don'?t (?:let me )?forget (?:to )?(.+)", 0.85),
        (r"remember (?:to |that i need to )?(.+)", 0.75),
    ]

    # Patterns that indicate reminder intent
    REMINDER_PATTERNS = [
        (r"remind me (?:to )?(.+?)(?:\s+(?:at|in|on|tomorrow|tonight|later).*)?$", 0.95),
        (r"set a reminder (?:to |for )?(.+)", 0.95),
        (r"alert me (?:to |about )?(.+)", 0.90),
        # Note: "tell me" removed as too broad - matches "tell me a joke" etc.
    ]

    # Patterns for time extraction
    # Order matters! More specific patterns (tomorrow at X) must come before generic (at X)
    TIME_PATTERNS = [
        # Relative times
        (r"in (\d+) minutes?", "minutes"),
        (r"in (\d+) hours?", "hours"),
        (r"in (\d+) days?", "days"),
        (r"in an? hour", "hour_single"),
        (r"in half an hour", "half_hour"),
        
        # Relative days WITH times (must be before "at X" pattern)
        (r"tomorrow\s+(?:at\s+)?(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", "tomorrow_time"),
        (r"tomorrow", "tomorrow"),
        (r"today\s+(?:at\s+)?(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", "today_time"),
        
        # Named times (absolute)
        (r"at (\d{1,2})(?::(\d{2}))?\s*(am|pm)?", "absolute"),
        (r"at noon", "noon"),
        (r"at midnight", "midnight"),
        (r"tonight", "tonight"),
        (r"this evening", "evening"),
        (r"this afternoon", "afternoon"),
        (r"this morning", "morning"),
        
        # Other relative
        (r"next week", "next_week"),
        (r"in a week", "week"),
        
        # Days of week
        (r"(?:on )?(monday|tuesday|wednesday|thursday|friday|saturday|sunday)", "weekday"),
    ]

    # Query patterns
    QUERY_PATTERNS = [
        (r"what(?:'?s| is) on my (?:list|tasks?|todo)", CaptureIntent.QUERY_TASKS, 0.95),
        (r"(?:show|list|read)(?: me)?(?: my)? (?:list|tasks?|todos?)", CaptureIntent.QUERY_TASKS, 0.90),
        (r"what do i (?:need|have) to do", CaptureIntent.QUERY_TASKS, 0.85),
        (r"what(?:'s| is) (?:up|happening) today", CaptureIntent.QUERY_TODAY, 0.85),
        (r"what do i (?:need|have) to do today", CaptureIntent.QUERY_TODAY, 0.95),
        (r"what(?:'s| is) (?:on )?(?:my )?(?:schedule|agenda)(?: (?:for )?today)?", CaptureIntent.QUERY_TODAY, 0.90),
        (r"(?:what(?:'s| is) the )?status (?:of|on) (.+)", CaptureIntent.QUERY_PROJECT, 0.90),
        (r"how(?:'s| is) (.+) (?:going|coming along|progressing)", CaptureIntent.QUERY_PROJECT, 0.85),
    ]

    # Completion patterns
    COMPLETE_PATTERNS = [
        (r"(?:i )?(?:finished|completed|done with|did) (.+)", 0.85),
        (r"mark (.+) (?:as )?(?:done|complete|finished)", 0.95),
        (r"check off (.+)", 0.90),
        (r"(.+) is (?:done|complete|finished)", 0.80),
    ]

    def __init__(self):
        """Initialize the capture pipeline."""
        # Compile all patterns
        self._task_patterns = [
            (re.compile(p, re.IGNORECASE), conf) 
            for p, conf in self.TASK_PATTERNS
        ]
        self._reminder_patterns = [
            (re.compile(p, re.IGNORECASE), conf)
            for p, conf in self.REMINDER_PATTERNS
        ]
        self._time_patterns = [
            (re.compile(p, re.IGNORECASE), kind)
            for p, kind in self.TIME_PATTERNS
        ]
        self._query_patterns = [
            (re.compile(p, re.IGNORECASE), intent, conf)
            for p, intent, conf in self.QUERY_PATTERNS
        ]
        self._complete_patterns = [
            (re.compile(p, re.IGNORECASE), conf)
            for p, conf in self.COMPLETE_PATTERNS
        ]

    def parse(self, text: str) -> ParsedCapture:
        """
        Parse an utterance and extract intent + entities.
        
        Args:
            text: The transcribed speech
            
        Returns:
            ParsedCapture with detected intent and entities
        """
        text = text.strip()
        
        # Check for queries first (they're quick lookups)
        for pattern, intent, confidence in self._query_patterns:
            match = pattern.search(text)
            if match:
                result = ParsedCapture(intent=intent, confidence=confidence)
                if intent == CaptureIntent.QUERY_PROJECT and match.groups():
                    result.project_name = match.group(1).strip()
                logger.debug(f"Matched query: {intent.value} ({confidence:.0%})")
                return result

        # Check for completion
        for pattern, confidence in self._complete_patterns:
            match = pattern.search(text)
            if match:
                result = ParsedCapture(
                    intent=CaptureIntent.COMPLETE_TASK,
                    confidence=confidence,
                    task_reference=match.group(1).strip(),
                )
                logger.debug(f"Matched completion: {result.task_reference}")
                return result

        # Check for reminders (before tasks, since "remind me to X" should be reminder not task)
        for pattern, confidence in self._reminder_patterns:
            match = pattern.search(text)
            if match:
                message = match.group(1).strip()
                trigger_time = self._parse_time(text)
                
                result = ParsedCapture(
                    intent=CaptureIntent.REMINDER,
                    confidence=confidence,
                    reminder_message=message,
                    trigger_time=trigger_time,
                    entities={"raw_time": self._extract_time_phrase(text)},
                )
                logger.debug(f"Matched reminder: {message} at {trigger_time}")
                return result

        # Check for tasks
        for pattern, confidence in self._task_patterns:
            match = pattern.search(text)
            if match:
                title = match.group(1).strip()
                # Clean up the title
                title = self._clean_task_title(title)
                
                result = ParsedCapture(
                    intent=CaptureIntent.TASK,
                    confidence=confidence,
                    task_title=title,
                )
                logger.debug(f"Matched task: {title}")
                return result

        # Unknown intent
        logger.debug(f"No intent matched for: {text[:50]}...")
        return ParsedCapture(intent=CaptureIntent.UNKNOWN, confidence=0.0)

    def _parse_time(self, text: str) -> Optional[datetime]:
        """
        Extract and parse time from text.
        
        Returns:
            datetime for the trigger time, or None if no time specified
        """
        now = datetime.now()
        
        for pattern, kind in self._time_patterns:
            match = pattern.search(text)
            if not match:
                continue
            
            try:
                if kind == "minutes":
                    minutes = int(match.group(1))
                    return now + timedelta(minutes=minutes)
                    
                elif kind == "hours":
                    hours = int(match.group(1))
                    return now + timedelta(hours=hours)
                    
                elif kind == "days":
                    days = int(match.group(1))
                    return now + timedelta(days=days)
                    
                elif kind == "hour_single":
                    return now + timedelta(hours=1)
                    
                elif kind == "half_hour":
                    return now + timedelta(minutes=30)
                    
                elif kind == "noon":
                    return now.replace(hour=12, minute=0, second=0, microsecond=0)
                    
                elif kind == "midnight":
                    tomorrow = now + timedelta(days=1)
                    return tomorrow.replace(hour=0, minute=0, second=0, microsecond=0)
                    
                elif kind == "tonight":
                    return now.replace(hour=20, minute=0, second=0, microsecond=0)
                    
                elif kind == "evening":
                    return now.replace(hour=18, minute=0, second=0, microsecond=0)
                    
                elif kind == "afternoon":
                    return now.replace(hour=14, minute=0, second=0, microsecond=0)
                    
                elif kind == "morning":
                    if now.hour >= 12:
                        # If it's past noon, morning means tomorrow
                        tomorrow = now + timedelta(days=1)
                        return tomorrow.replace(hour=9, minute=0, second=0, microsecond=0)
                    return now.replace(hour=9, minute=0, second=0, microsecond=0)
                    
                elif kind == "tomorrow_time":
                    # Tomorrow with specific time
                    tomorrow = now + timedelta(days=1)
                    hour, minute = self._parse_hhmm(match)
                    if hour is not None:
                        return tomorrow.replace(hour=hour, minute=minute or 0, second=0, microsecond=0)
                    return tomorrow.replace(hour=9, minute=0, second=0, microsecond=0)
                
                elif kind == "tomorrow":
                    # Tomorrow without specific time
                    tomorrow = now + timedelta(days=1)
                    return tomorrow.replace(hour=9, minute=0, second=0, microsecond=0)
                    
                elif kind == "today_time":
                    # Today with specific time
                    hour, minute = self._parse_hhmm(match)
                    if hour is not None:
                        return now.replace(hour=hour, minute=minute or 0, second=0, microsecond=0)
                    return None
                    
                elif kind == "next_week" or kind == "week":
                    return now + timedelta(weeks=1)
                    
                elif kind == "weekday":
                    day_name = match.group(1).lower()
                    return self._next_weekday(day_name)
                    
                elif kind == "absolute":
                    hour, minute = self._parse_hhmm(match)
                    if hour is not None:
                        result = now.replace(hour=hour, minute=minute or 0, second=0, microsecond=0)
                        # If time is in the past, assume tomorrow
                        if result <= now:
                            result += timedelta(days=1)
                        return result
                        
            except (ValueError, AttributeError) as e:
                logger.warning(f"Failed to parse time ({kind}): {e}")
                continue
        
        return None

    def _parse_hhmm(self, match) -> Tuple[Optional[int], Optional[int]]:
        """Parse hour and minute from regex match groups."""
        groups = match.groups()
        
        # Find hour and minute in groups
        hour = None
        minute = None
        ampm = None
        
        for g in groups:
            if g is None:
                continue
            if g.lower() in ("am", "pm"):
                ampm = g.lower()
            elif hour is None:
                try:
                    hour = int(g)
                except ValueError:
                    pass
            elif minute is None:
                try:
                    minute = int(g)
                except ValueError:
                    pass
        
        if hour is None:
            return None, None
        
        # Handle AM/PM
        if ampm == "pm" and hour < 12:
            hour += 12
        elif ampm == "am" and hour == 12:
            hour = 0
        elif ampm is None and hour < 8:
            # Assume PM for times like "at 3" (probably 3pm, not 3am)
            hour += 12
        
        return hour, minute

    def _next_weekday(self, day_name: str) -> datetime:
        """Get the next occurrence of a weekday."""
        days = {
            "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
            "friday": 4, "saturday": 5, "sunday": 6
        }
        target = days.get(day_name, 0)
        now = datetime.now()
        current = now.weekday()
        
        days_ahead = target - current
        if days_ahead <= 0:  # Target day already happened this week
            days_ahead += 7
        
        result = now + timedelta(days=days_ahead)
        return result.replace(hour=9, minute=0, second=0, microsecond=0)

    def _extract_time_phrase(self, text: str) -> Optional[str]:
        """Extract the time-related phrase from text for logging."""
        time_words = [
            "at", "in", "on", "tomorrow", "tonight", "today",
            "morning", "afternoon", "evening", "noon", "midnight",
            "monday", "tuesday", "wednesday", "thursday", "friday",
            "saturday", "sunday", "next week"
        ]
        
        words = text.lower().split()
        for i, word in enumerate(words):
            if word in time_words:
                return " ".join(words[i:])
        return None

    def _clean_task_title(self, title: str) -> str:
        """Clean up extracted task title."""
        # Remove trailing time phrases
        time_words = [
            "at", "by", "before", "tomorrow", "today", "tonight",
            "this morning", "this afternoon", "this evening"
        ]
        
        title_lower = title.lower()
        for word in time_words:
            if title_lower.endswith(word):
                title = title[:-len(word)].strip()
                break
        
        # Remove trailing punctuation
        title = title.rstrip(".,!?")
        
        # Capitalize first letter
        if title:
            title = title[0].upper() + title[1:]
        
        return title


# Module-level instance for convenience
_pipeline: Optional[CapturePipeline] = None


def get_capture_pipeline() -> CapturePipeline:
    """Get or create the capture pipeline singleton."""
    global _pipeline
    if _pipeline is None:
        _pipeline = CapturePipeline()
    return _pipeline


def parse_utterance(text: str) -> ParsedCapture:
    """Convenience function to parse an utterance."""
    return get_capture_pipeline().parse(text)
