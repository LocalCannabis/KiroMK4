"""
jack — Kiro's master grower persona for Tim's indoor cannabis cultivation.

Provides the Jack persona with grow state tracking, environmental checkin
protocol, knowledge-grounded advice, and voice-first interaction through
the existing Kiro audio pipeline.

Named after Jack Herer — cannabis activist and author of
*The Emperor Wears No Clothes*.
"""

from .config import load_jack_config
from .db import JackDB

__all__ = [
    "load_jack_config",
    "JackDB",
]
