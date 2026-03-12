"""
tools/registry.py — OpenAI function-calling schemas and tool dispatch.

Usage in KiroOrchestrator:
    registry = ToolRegistry(cfg, notification_queue, logger)
    schemas   = registry.schemas()           # pass to OpenAI as 'tools='
    result    = registry.execute(name, args) # call after model picks a tool
"""

from __future__ import annotations

import logging
import queue
from typing import Any, Dict, List

from .timer import set_timer
from .calculator import calculate
from .weather import get_weather
from .notes_lists import NotesAndLists


# ---------------------------------------------------------------------------
# Tool schemas (OpenAI function-calling format)
# ---------------------------------------------------------------------------

_SCHEMAS: List[Dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "set_timer",
            "description": "Set a countdown timer that will notify Tim when done.",
            "parameters": {
                "type": "object",
                "properties": {
                    "seconds": {
                        "type": "integer",
                        "description": "Duration in seconds. Convert minutes/hours as needed.",
                    },
                    "label": {
                        "type": "string",
                        "description": "What the timer is for, e.g. 'check the soil' or 'pasta'.",
                        "default": "",
                    },
                },
                "required": ["seconds"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_note",
            "description": "Save a note or piece of information for Tim.",
            "parameters": {
                "type": "object",
                "properties": {
                    "content": {"type": "string", "description": "The note content."},
                    "tags": {
                        "type": "string",
                        "description": "Optional comma-separated tags, e.g. 'grow,cannabis'.",
                        "default": "",
                    },
                },
                "required": ["content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_add",
            "description": "Add an item to a named list (grocery, todo, materials, etc.).",
            "parameters": {
                "type": "object",
                "properties": {
                    "list_name": {"type": "string", "description": "Name of the list."},
                    "item": {"type": "string", "description": "Item to add."},
                },
                "required": ["list_name", "item"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_remove",
            "description": "Remove an item from a named list.",
            "parameters": {
                "type": "object",
                "properties": {
                    "list_name": {"type": "string", "description": "Name of the list."},
                    "item": {"type": "string", "description": "Item to remove."},
                },
                "required": ["list_name", "item"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_get",
            "description": "Read out everything on a named list.",
            "parameters": {
                "type": "object",
                "properties": {
                    "list_name": {"type": "string", "description": "Name of the list."},
                },
                "required": ["list_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_clear",
            "description": "Clear all items from a named list.",
            "parameters": {
                "type": "object",
                "properties": {
                    "list_name": {"type": "string", "description": "Name of the list."},
                },
                "required": ["list_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "calculate",
            "description": "Evaluate a math expression or unit conversion.",
            "parameters": {
                "type": "object",
                "properties": {
                    "expression": {
                        "type": "string",
                        "description": "A math expression, e.g. '2 * 8 + 3' or 'sqrt(144)'.",
                    },
                },
                "required": ["expression"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "Get current weather conditions in Vancouver, BC.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
]


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

class ToolRegistry:
    def __init__(
        self,
        cfg: Dict[str, Any],
        notification_queue: queue.Queue,
        logger: logging.Logger,
    ) -> None:
        self.logger = logger
        self._nq = notification_queue
        self._enabled = cfg.get("tools", {}).get("enabled", False)
        self._allow_list = set(cfg.get("tools", {}).get("allow_list", []))

        db_path = cfg.get("memory", {}).get("sqlite_path", "./data/kiro.db")
        self._nl = NotesAndLists(db_path)

        if self._enabled:
            self.logger.info("Tools enabled: %s", sorted(self._allow_list))
        else:
            self.logger.info("Tools disabled (tools.enabled=false in config).")

    def schemas(self) -> List[Dict[str, Any]]:
        """Return OpenAI-formatted tool schemas for enabled tools only."""
        if not self._enabled:
            return []
        def _allowed(tool_name: str) -> bool:
            # Exact match OR any allow_list keyword appears in the tool name
            return tool_name in self._allow_list or any(
                kw in tool_name for kw in self._allow_list
            )

        return [s for s in _SCHEMAS if _allowed(s["function"]["name"])]

    def execute(self, name: str, args: Dict[str, Any]) -> str:
        """Dispatch a tool call by name. Returns a result string."""
        self.logger.info("Tool call: %s(%s)", name, args)
        try:
            return self._dispatch(name, args)
        except Exception as exc:
            self.logger.error("Tool '%s' failed: %s", name, exc)
            return f"Tool error: {exc}"

    def _dispatch(self, name: str, args: Dict[str, Any]) -> str:
        if name == "set_timer":
            return set_timer(
                seconds=int(args.get("seconds", 60)),
                label=args.get("label", ""),
                notification_queue=self._nq,
                logger=self.logger,
            )
        if name == "add_note":
            return self._nl.add_note(args["content"], args.get("tags", ""))
        if name == "list_add":
            return self._nl.add_item(args["list_name"], args["item"])
        if name == "list_remove":
            return self._nl.remove_item(args["list_name"], args["item"])
        if name == "list_get":
            return self._nl.get_list(args["list_name"])
        if name == "list_clear":
            return self._nl.clear_list(args["list_name"])
        if name == "calculate":
            return calculate(args["expression"])
        if name == "get_weather":
            return get_weather()
        return f"Unknown tool: {name}"
