"""
KIRO UI — Flask Application

Serves the chat interface and provides API endpoints for session
management, message history, and LLM chat with real persona
prompts, tool schemas, and tool execution — the same infrastructure
the voice loop uses.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
import time
import urllib.request
import urllib.error
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

from flask import (
    Flask,
    Response,
    jsonify,
    render_template,
    request,
    stream_with_context,
)
from openai import OpenAI

from config import (
    APP_TITLE,
    DATABASE_URL,
    FLASK_PORT,
    LLM_CONFIG,
    MODEL_ROUTING,
    PERSONA_ORDER,
    PERSONAS,
    PROJECT_ROOT,
    SECRET_KEY,
)
from models import ChatMessage, ChatSession, db

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
)
log = logging.getLogger("kiro.ui")

# ---------------------------------------------------------------------------
# Optional persona module imports (same guard pattern as kiro.py)
# ---------------------------------------------------------------------------
_finley_available = False
_finley_sync_daemon = None
try:
    from finley.prompts import get_finley_system_prompt
    from finley.intent_router import (
        FINLEY_TOOL_SCHEMAS,
        execute_finley_tool,
        get_pending_insights_text,
    )
    from finley.db import FinleyDB
    from finley.sync import SyncDaemon as FinleySyncDaemon, default_post_sync as finley_post_sync

    _finley_available = True
    log.info("Finley module loaded — real prompts + %d tools", len(FINLEY_TOOL_SCHEMAS))
except ImportError as e:
    log.warning("Finley module not available: %s", e)

_jack_available = False
try:
    from jack.prompts import get_jack_system_prompt
    from jack.intent_router import (
        JACK_TOOL_SCHEMAS,
        execute_jack_tool,
        get_jack_context_for_prompt,
    )
    from jack.db import JackDB
    from jack.config import load_jack_config

    _jack_available = True
    log.info("Jack module loaded — real prompts + %d tools", len(JACK_TOOL_SCHEMAS))
except ImportError as e:
    log.warning("Jack module not available: %s", e)

_coach_available = False
try:
    from coach.prompts import get_coach_system_prompt
    from coach.intent_router import (
        COACH_TOOL_SCHEMAS,
        execute_coach_tool,
    )
    from coach.db import CoachDB

    _coach_available = True
    log.info("Coach module loaded — real prompts + %d tools", len(COACH_TOOL_SCHEMAS))
except ImportError as e:
    log.warning("Coach module not available: %s", e)

_ambient_available = False
try:
    from ambient.db import AmbientDB

    _ambient_available = True
    log.info("Ambient intelligence layer loaded")
except ImportError:
    pass

# ---------------------------------------------------------------------------
# Proactive Catchup — state tracking
# ---------------------------------------------------------------------------
_catchup_state = {
    "last_offered_at": None,   # datetime of last catchup check that returned available
    "last_completed_at": None, # datetime of last catchup actually delivered
    "cooldown_hours": 4,       # minimum hours between catchups
}


def _gather_catchup_context(persona_key: str = "kiro") -> Dict[str, Any]:
    """
    Gather context from all available data sources for a proactive catchup.
    Each source is independently faulted — if Google auth fails, we still
    get chat history and ambient insights.

    `persona_key` filters ambient insights to only those relevant to the
    active persona (or cross-cutting insights with persona=NULL/kiro).
    """
    ctx: Dict[str, Any] = {
        "time_of_day": "",
        "calendar": "",
        "email": "",
        "weather": "",
        "insights": [],
        "recent_chats": [],
        "finley": "",
    }

    now = datetime.now()
    hour = now.hour
    if hour < 12:
        ctx["time_of_day"] = "morning"
    elif hour < 17:
        ctx["time_of_day"] = "afternoon"
    else:
        ctx["time_of_day"] = "evening"

    # ── Calendar ──
    try:
        from tools.google_calendar import list_calendar_events
        ctx["calendar"] = list_calendar_events(time_range="today")
    except Exception as e:
        log.debug("Catchup: calendar unavailable: %s", e)

    # ── Email ──
    try:
        from tools.gmail import read_emails
        ctx["email"] = read_emails(max_results=5, query="is:unread")
    except Exception as e:
        log.debug("Catchup: email unavailable: %s", e)

    # ── Weather ──
    try:
        from tools.weather import get_weather
        ctx["weather"] = get_weather()
    except Exception as e:
        log.debug("Catchup: weather unavailable: %s", e)

    # ── Ambient insights — filtered to active persona ──
    if _ambient_available:
        try:
            adb = AmbientDB()
            raw = adb.get_unsurfaced_insights(max_priority=7, limit=20)
            ctx["insights"] = [
                {"persona": i.get("persona", "kiro"),
                 "type": i.get("insight_type", ""),
                 "summary": i.get("summary", ""),
                 "priority": i.get("priority", 5)}
                for i in raw
                # For non-kiro personas: only include this persona's insights
                # or cross-cutting ones (persona=None/"kiro")
                if persona_key == "kiro"
                or i.get("persona") in (persona_key, None, "", "kiro")
            ]
        except Exception as e:
            log.debug("Catchup: ambient insights unavailable: %s", e)

    # ── Recent chat activity (last 24h) — filtered to active persona ──
    try:
        query = (
            ChatSession.query
            .filter(ChatSession.updated_at >= now - timedelta(hours=24))
        )
        # For non-kiro personas, only surface sessions for that persona
        if persona_key != "kiro":
            query = query.filter(ChatSession.persona_key == persona_key)
        recent_sessions = (
            query
            .order_by(ChatSession.updated_at.desc())
            .limit(5)
            .all()
        )
        for s in recent_sessions:
            last_msg = (
                ChatMessage.query
                .filter_by(session_id=s.id)
                .order_by(ChatMessage.created_at.desc())
                .first()
            )
            if last_msg:
                ctx["recent_chats"].append({
                    "persona": s.persona_key,
                    "title": s.title,
                    "preview": last_msg.content[:200],
                })
    except Exception as e:
        log.debug("Catchup: chat history unavailable: %s", e)

    # ── Finley proactive insights ──
    if _finley_available:
        try:
            ctx["finley"] = get_pending_insights_text() or ""
        except Exception as e:
            log.debug("Catchup: finley insights unavailable: %s", e)

    return ctx


def _catchup_has_content(ctx: Dict[str, Any]) -> bool:
    """Return True if there's enough data to justify a catchup."""
    cal = ctx.get("calendar", "")
    has_calendar = bool(cal) and "nothing scheduled" not in cal.lower()
    email = ctx.get("email", "")
    has_email = bool(email) and "no unread" not in email.lower()
    has_insights = len(ctx.get("insights", [])) > 0
    has_finley = bool(ctx.get("finley", ""))
    has_chats = len(ctx.get("recent_chats", [])) > 0
    return has_calendar or has_email or has_insights or has_finley or has_chats


# ---------------------------------------------------------------------------
# LLM clients — routed (OpenRouter/Claude) + fallback (OpenAI)
# ---------------------------------------------------------------------------
_api_key = os.getenv(LLM_CONFIG.get("api_key_env", "OPENAI_API_KEY"), "")
_client_kwargs: Dict[str, Any] = {"api_key": _api_key}
if LLM_CONFIG.get("base_url"):
    _client_kwargs["base_url"] = LLM_CONFIG["base_url"]

llm_client: Optional[OpenAI] = None
if _api_key:
    try:
        llm_client = OpenAI(**_client_kwargs)
        log.info(
            "OpenAI client ready — model=%s base_url=%s",
            LLM_CONFIG["model"],
            LLM_CONFIG.get("base_url", "default"),
        )
    except Exception as exc:
        log.error("Failed to create OpenAI client: %s", exc)

# Routed client — Anthropic SDK (Claude models) if key is present, else falls
# back to the OpenAI client above.
_routing_key = os.getenv(MODEL_ROUTING.get("api_key_env", "ANTHROPIC_API_KEY"), "")
_routed_client = None   # will be anthropic.Anthropic() if available
if _routing_key:
    try:
        import anthropic as _anthropic_sdk
        _routed_client = _anthropic_sdk.Anthropic(api_key=_routing_key)
        log.info(
            "Anthropic client ready — fast=%s balanced=%s deep=%s",
            MODEL_ROUTING["models"]["fast"],
            MODEL_ROUTING["models"]["balanced"],
            MODEL_ROUTING["models"]["deep"],
        )
    except Exception as exc:
        log.warning("Failed to create Anthropic client: %s — falling back to OpenAI", exc)
else:
    log.info(
        "No ANTHROPIC_API_KEY set — UI chat will use OpenAI fallback (%s)",
        LLM_CONFIG.get("model", "gpt-4o"),
    )


# ---------------------------------------------------------------------------
# Model complexity routing
# ---------------------------------------------------------------------------

# Patterns that strongly signal a deep/strategic conversation is needed
_DEEP_RE = re.compile(
    r"\b("
    r"let'?s (sit down|talk|have a|do (this|that))|serious(ly)?|from start to finish|"
    r"walk me through|help me (build|create|plan|set up|figure out|understand|think through)|"
    r"(talk|think|go) (about|through)|discuss|strategize|strategy|big picture|"
    r"overhaul|comprehensive|thorough|once and for all|properly|actually commit|"
    r"make a (plan|budget|schedule)|set (a |my )?(budget|goal|plan)|"
    r"sit down (and|with)|break (it|this) down|explain (everything|how|why)|"
    r"i need (to understand|help with|advice on)|long.?term|going forward|"
    r"what should i (do|change|focus)|how (do i|can i|should i)"
    r")\b",
    re.IGNORECASE,
)

# Patterns for fast factual lookups
_FAST_RE = re.compile(
    r"\b("
    r"how much (did|have|has)|what (is|are|was|were|did|does)|show me|list|display|"
    r"when (is|was|did|does)|total|balance|spent|spending|transactions?|"
    r"did i|have i|last (week|month|year)|this (week|month|year)|"
    r"today|yesterday|quick|what'?s (my|the)|how many|current"
    r")\b",
    re.IGNORECASE,
)

_TIER_ORDER = ["fast", "balanced", "deep"]


def _classify_complexity(message: str, history_len: int, persona: str) -> str:
    """
    Classify a message into a model tier: 'fast', 'balanced', or 'deep'.

    Architect / Builder model:
    - Opus  (deep)     — strategic planning, deep analysis, any time a deep
                         pattern fires regardless of where we are in the session
    - Sonnet (balanced) — execution, follow-up steps, anything mid-conversation
                          that isn't a quick fact lookup
    - Haiku (fast)     — quick factual lookups in fresh sessions only (≤4 turns)

    Haiku is never used once a conversation has meaningful depth — the natural
    Opus → Sonnet handoff happens because execution messages don't re-trigger
    the deep-pattern regex.

    The result is then floored to the persona's configured minimum tier.
    """
    if _DEEP_RE.search(message):
        tier = "deep"
    elif _FAST_RE.search(message) and len(message) < 80 and history_len <= 4:
        # Quick lookup in a fresh/shallow session only
        tier = "fast"
    else:
        tier = "balanced"

    # Apply persona floor
    floor = MODEL_ROUTING.get("persona_floor", {}).get(persona, "fast")
    if _TIER_ORDER.index(floor) > _TIER_ORDER.index(tier):
        tier = floor

    return tier


def _get_model_for_tier(tier: str) -> str:
    """Return the model name for a given tier, using routing config."""
    return MODEL_ROUTING["models"].get(tier, MODEL_ROUTING["models"]["balanced"])


def _get_chat_client():
    """
    Return (client, provider) where provider is 'anthropic' or 'openai'.
    Prefers Anthropic if configured, falls back to OpenAI.
    """
    if _routed_client is not None:
        return _routed_client, "anthropic"
    if llm_client is not None:
        return llm_client, "openai"
    return None, None

# ---------------------------------------------------------------------------
# Fallback persona prompts (for personas without dedicated modules)
# ---------------------------------------------------------------------------
_FALLBACK_PROMPTS: Dict[str, str] = {
    "kiro": (
        "You are Kiro (pronounced Key-Row), Tim's always-on personal AI hub. "
        "You are the home base — calm, direct, slightly witty, and always present. "
        "You coordinate access to a team of specialists: Finley (finance), Chef (cooking), "
        "Coach (executive function), Doc (wellbeing), Sage (debate), Jack (cultivation). "
        "When Tim asks to switch personas, acknowledge it naturally. "
        "You have FULL access to Tim's Google Workspace: Calendar, Gmail, Google Drive, "
        "Google Docs, and Google Sheets. Use these tools proactively when relevant. "
        "You also have awareness of Tim's life context through the ambient intelligence layer."
    ),
    "chef": (
        "You are Chef, Tim's culinary guide. "
        "Warm, enthusiastic, and practical. Help with recipes, ingredients, meal planning, "
        "and grocery lists. Tim lives in Vancouver, BC."
    ),
    "doc": (
        "You are Doc, Tim's wellbeing companion. "
        "Gentle, reflective, and Socratic. Help Tim process stress and emotions "
        "without giving clinical advice."
    ),
    "sage": (
        "You are Sage, Tim's intellectual sparring partner. "
        "Provocative, curious, and never giving easy answers. Challenge assumptions "
        "and make Tim think."
    ),
}


# ---------------------------------------------------------------------------
# Prompt builders — dynamic, live-data-injected, same as the voice loop
# ---------------------------------------------------------------------------
def _get_ambient_context(persona: str) -> str:
    """Pull recent unsurfaced insights for the current persona."""
    if not _ambient_available:
        return ""
    try:
        adb = AmbientDB()
        conn = adb._conn()
        try:
            import psycopg2.extras

            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT summary, insight_type, priority, persona
                    FROM kiro_insights
                    WHERE (persona = %s OR persona IS NULL)
                      AND surfaced = FALSE
                      AND dismissed = FALSE
                      AND priority <= 7
                      AND created_at > NOW() - INTERVAL '24 hours'
                    ORDER BY priority ASC
                    LIMIT 5
                    """,
                    (persona,),
                )
                insights = cur.fetchall()
                if insights:
                    lines = [
                        "Recent ambient insights relevant to your domain — "
                        "reference proactively if relevant:"
                    ]
                    for ins in insights:
                        owner = ins["persona"] or "Kiro"
                        lines.append(
                            f"- [{owner}/{ins['insight_type']}] {ins['summary']}"
                        )
                    return "\n".join(lines)
        finally:
            adb._put(conn)
    except Exception:
        pass
    return ""


def build_system_prompt(persona: str) -> str:
    """
    Build the full system prompt for a persona.

    For Finley/Coach/Jack this calls the real prompt generators with live
    DB state — the exact same prompts the voice loop uses.  For other
    personas we use the fallback strings.

    The text UI suffix is different from the voice loop: we allow markdown,
    longer responses, and richer formatting since the user is reading, not
    listening.
    """
    now = datetime.now().strftime("%A, %B %d %Y, %I:%M %p")

    # --- Persona-specific dynamic prompts ---
    if persona == "finley" and _finley_available:
        try:
            insights = get_pending_insights_text()
            persona_text = get_finley_system_prompt(insights_text=insights)
        except Exception as exc:
            log.warning("Finley prompt build failed, using fallback: %s", exc)
            persona_text = _FALLBACK_PROMPTS.get("kiro", "")
    elif persona == "jack" and _jack_available:
        try:
            ctx = get_jack_context_for_prompt()
            persona_text = get_jack_system_prompt(
                indoor_snapshot=ctx.get("indoor_snapshot", ""),
                outdoor_snapshot=ctx.get("outdoor_snapshot", ""),
                knowledge_context=ctx.get("knowledge_context", ""),
                active_flags=ctx.get("active_flags", ""),
            )
        except Exception as exc:
            log.warning("Jack prompt build failed, using fallback: %s", exc)
            persona_text = _FALLBACK_PROMPTS.get("kiro", "")
    elif persona == "coach" and _coach_available:
        try:
            coach_db = CoachDB()
            persona_text = get_coach_system_prompt(db=coach_db)
        except Exception as exc:
            log.warning("Coach prompt build failed, using fallback: %s", exc)
            persona_text = _FALLBACK_PROMPTS.get("kiro", "")
    else:
        persona_text = _FALLBACK_PROMPTS.get(
            persona, _FALLBACK_PROMPTS["kiro"]
        )

    # Ambient context injection
    ambient = _get_ambient_context(persona)

    # Text-UI suffix — no voice constraints, allow rich markdown
    prompt = (
        f"{persona_text}\n\n"
        f"Current date and time: {now} (Vancouver, BC, Canada).\n"
        "You are responding in a text chat interface. You may use markdown "
        "formatting (bold, lists, headers, code blocks) when it helps clarity. "
        "Be thorough but don't pad — Tim appreciates directness."
    )
    if ambient:
        prompt += f"\n\n{ambient}"
    return prompt


def get_persona_tools(
    persona: str,
) -> Tuple[Optional[List[Dict]], Optional[Callable]]:
    """
    Return (tool_schemas, executor_fn) for a persona.

    Returns (None, None) for personas without tools.
    """
    if persona == "finley" and _finley_available:
        return FINLEY_TOOL_SCHEMAS, execute_finley_tool
    if persona == "jack" and _jack_available:
        return JACK_TOOL_SCHEMAS, execute_jack_tool
    if persona == "coach" and _coach_available:
        return COACH_TOOL_SCHEMAS, execute_coach_tool
    return None, None


# ---------------------------------------------------------------------------
# Flask app setup
# ---------------------------------------------------------------------------
app = Flask(__name__)
app.config["SECRET_KEY"] = SECRET_KEY
app.config["SQLALCHEMY_DATABASE_URI"] = DATABASE_URL
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["TEMPLATES_AUTO_RELOAD"] = True

db.init_app(app)

with app.app_context():
    db.create_all()

# Wire bearer token auth for remote clients (localhost is always allowed)
from auth import init_auth
init_auth(app)

# Register Jack grow sensor API blueprint
try:
    from jack.grow_api import grow_bp
    app.register_blueprint(grow_bp, url_prefix="/api/grow")
    log.info("Jack grow sensor API registered at /api/grow")
except ImportError as e:
    log.warning("Jack grow API not available: %s", e)

# Start Finley background sync so the UI always has fresh YNAB data.
# Runs immediately on boot, then every 30 minutes.
# Guard: when Werkzeug reloader is active it forks a watcher process
# (WERKZEUG_RUN_MAIN=false) AND a worker (WERKZEUG_RUN_MAIN=true).
# Only skip the daemon in the watcher — start it in all other cases
# (normal direct run, or the actual reloader worker).
_in_reloader_watcher = os.environ.get("WERKZEUG_RUN_MAIN") == "false"
if _finley_available and not _in_reloader_watcher:
    try:
        _finley_sync_daemon = FinleySyncDaemon(
            interval_minutes=30,
            post_sync_callback=finley_post_sync,
            run_on_start=True,
        )
        _finley_sync_daemon.start()
        log.info("Finley sync daemon started (30-min interval, run_on_start=True)")
    except Exception as _exc:
        log.warning("Failed to start Finley sync daemon: %s", _exc)

# Start Jack grow-tent monitor daemon (checks sensors every 30s).
_grow_monitor_daemon = None
if not _in_reloader_watcher:
    try:
        from jack.grow_monitor import GrowMonitorDaemon
        _grow_monitor_daemon = GrowMonitorDaemon(
            check_interval_s=30,
            auto_relay=True,
        )
        _grow_monitor_daemon.start()
        log.info("Jack grow monitor daemon started (30s interval, auto_relay=True)")
    except Exception as _exc:
        log.warning("Failed to start grow monitor daemon: %s", _exc)


# ---------------------------------------------------------------------------
# Page routes
# ---------------------------------------------------------------------------
@app.route("/")
def index():
    return render_template(
        "index.html",
        personas=PERSONAS,
        persona_order=PERSONA_ORDER,
    )


@app.route("/hud")
def hud():
    """Jarvis-style transparent overlay HUD — served into GTK WebKit."""
    return render_template(
        "hud.html",
        personas=PERSONAS,
        persona_order=PERSONA_ORDER,
    )


@app.route("/grow")
def grow_dashboard():
    """Real-time grow tent monitoring dashboard."""
    return render_template("grow.html")


# ---------------------------------------------------------------------------
# API — Health + remote client session continuity
# ---------------------------------------------------------------------------
@app.route("/api/health")
def api_health():
    """
    Liveness check used by the macOS client to detect Beast reachability.
    Returns uptime, persona count, and a timestamp.
    """
    import time as _time
    return jsonify({
        "ok": True,
        "service": "kiro-beast",
        "personas": len(PERSONAS),
        "ts": datetime.now(timezone.utc).isoformat(),
    })


@app.route("/api/session/state")
def api_session_state():
    """
    Return the most-recent active session per persona so a remote client
    (iMac) can resume exactly where things left off.

    Response:
      {
        "active_persona": "coach",          # persona with most recent activity
        "personas": {
          "coach": {"session_id": 12, "last_message_id": 48, "has_unread": false},
          ...
        }
      }
    """
    state: Dict[str, Any] = {}
    latest_persona: Optional[str] = None
    latest_ts: Optional[datetime] = None

    for key in PERSONA_ORDER:
        session = (
            ChatSession.query
            .filter_by(persona_key=key, archived=False)
            .order_by(ChatSession.updated_at.desc())
            .first()
        )
        if not session:
            state[key] = {"session_id": None, "last_message_id": None, "has_unread": False}
            continue

        last_msg = (
            ChatMessage.query
            .filter_by(session_id=session.id)
            .order_by(ChatMessage.created_at.desc())
            .first()
        )
        state[key] = {
            "session_id": session.id,
            "last_message_id": last_msg.id if last_msg else None,
            "has_unread": False,  # extend later if needed
        }

        # Track which persona had the most recent activity
        if latest_ts is None or session.updated_at > latest_ts:
            latest_ts = session.updated_at
            latest_persona = key

    return jsonify({
        "active_persona": latest_persona or PERSONA_ORDER[0],
        "personas": state,
    })


# ---------------------------------------------------------------------------
# API — Personas
# ---------------------------------------------------------------------------
@app.route("/api/personas")
def api_personas():
    ordered = []
    for key in PERSONA_ORDER:
        p = PERSONAS[key].copy()
        p["key"] = key
        ordered.append(p)
    return jsonify(ordered)


# ---------------------------------------------------------------------------
# API — Sessions
# ---------------------------------------------------------------------------
@app.route("/api/sessions/<persona_key>")
def api_sessions(persona_key):
    sessions = (
        ChatSession.query.filter_by(persona_key=persona_key, archived=False)
        .order_by(ChatSession.updated_at.desc())
        .all()
    )
    return jsonify([s.to_dict(message_count=True) for s in sessions])


@app.route("/api/sessions", methods=["POST"])
def api_create_session():
    data = request.get_json()
    persona_key = data.get("persona_key")
    if persona_key not in PERSONAS:
        return jsonify({"error": "Unknown persona"}), 400
    session = ChatSession(
        persona_key=persona_key,
        title=data.get("title", "New Session"),
    )
    db.session.add(session)
    db.session.commit()
    return jsonify(session.to_dict()), 201


@app.route("/api/sessions/<int:session_id>", methods=["PATCH"])
def api_update_session(session_id):
    session = ChatSession.query.get_or_404(session_id)
    data = request.get_json()
    if "title" in data:
        session.title = data["title"][:200]
    if "archived" in data:
        session.archived = bool(data["archived"])
    db.session.commit()
    return jsonify(session.to_dict())


@app.route("/api/sessions/<int:session_id>", methods=["DELETE"])
def api_delete_session(session_id):
    session = ChatSession.query.get_or_404(session_id)
    db.session.delete(session)
    db.session.commit()
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# API — Messages
# ---------------------------------------------------------------------------
@app.route("/api/messages/<int:session_id>")
def api_messages(session_id):
    ChatSession.query.get_or_404(session_id)
    messages = (
        ChatMessage.query.filter_by(session_id=session_id)
        .order_by(ChatMessage.created_at)
        .all()
    )
    return jsonify([m.to_dict() for m in messages])


# ---------------------------------------------------------------------------
# API — Chat completion (streaming with real tool execution)
# ---------------------------------------------------------------------------
@app.route("/api/chat", methods=["POST"])
def api_chat():
    data = request.get_json()
    session_id = data.get("session_id")
    user_content = data.get("content", "").strip()

    if not user_content:
        return jsonify({"error": "Empty message"}), 400

    session = ChatSession.query.get_or_404(session_id)
    persona_key = session.persona_key
    persona = PERSONAS.get(persona_key)
    if not persona:
        return jsonify({"error": "Unknown persona"}), 400

    # Save user message
    user_msg = ChatMessage(
        session_id=session_id, role="user", content=user_content
    )
    db.session.add(user_msg)
    db.session.commit()

    # Auto-title from first message
    if session.title == "New Session":
        session.title = user_content[:100]
        db.session.commit()

    # No LLM client — return placeholder
    chat_client, provider = _get_chat_client()
    if not chat_client:
        placeholder = (
            f"[{persona['name']} is ready but no LLM API key is configured. "
            f"Set ANTHROPIC_API_KEY or {LLM_CONFIG.get('api_key_env', 'OPENAI_API_KEY')} "
            f"in your .env file.]"
        )
        assistant_msg = ChatMessage(
            session_id=session_id, role="assistant", content=placeholder
        )
        db.session.add(assistant_msg)
        session.updated_at = datetime.now(timezone.utc)
        db.session.commit()
        return jsonify({"message": assistant_msg.to_dict(), "streamed": False})

    # Build real system prompt (dynamic, live data injected)
    system_prompt = build_system_prompt(persona_key)

    # Build conversation history
    history = (
        ChatMessage.query.filter_by(session_id=session_id)
        .order_by(ChatMessage.created_at)
        .all()
    )
    llm_messages: List[Dict[str, Any]] = [
        {"role": "system", "content": system_prompt}
    ]
    for msg in history:
        llm_messages.append({"role": msg.role, "content": msg.content})

    # Route to appropriate model tier based on query complexity
    tier = _classify_complexity(user_content, len(history), persona_key)
    selected_model = _get_model_for_tier(tier)
    chat_client, provider = _get_chat_client()
    log.info("[%s] tier=%s model=%s provider=%s", persona_key, tier, selected_model, provider)

    # Get real tool schemas + executor for this persona
    tool_schemas, tool_executor = get_persona_tools(persona_key)

    def generate():
        """
        SSE generator.

        Streams the LLM response token-by-token. If the model requests
        tool calls, we execute them and do a second streaming pass with
        the tool results — same one-round-trip pattern as the voice loop.
        """
        full_response: List[str] = []
        t0 = time.perf_counter()

        try:
            # --- First LLM call (may include tools) ---
            temp = MODEL_ROUTING.get("temperature", LLM_CONFIG.get("temperature", 0.4))

            if provider == "anthropic":
                # Anthropic SDK: system is a top-level param, not a message
                anth_system = next(
                    (m["content"] for m in llm_messages if m["role"] == "system"), ""
                )
                anth_messages = [m for m in llm_messages if m["role"] != "system"]
                anth_tools = [
                    {
                        "name": t["function"]["name"],
                        "description": t["function"].get("description", ""),
                        "input_schema": t["function"].get("parameters", {"type": "object", "properties": {}}),
                    }
                    for t in (tool_schemas or [])
                ]
                create_kwargs: Dict[str, Any] = dict(
                    model=selected_model,
                    max_tokens=4096,
                    temperature=temp,
                    system=anth_system,
                    messages=anth_messages,
                )
                if anth_tools:
                    create_kwargs["tools"] = anth_tools

                tool_calls_acc = {}
                with chat_client.messages.stream(**create_kwargs) as stream:
                    for text in stream.text_stream:
                        full_response.append(text)
                        yield f"data: {json.dumps({'token': text})}\n\n"
                    final_msg = stream.get_final_message()

                # Check for tool use blocks
                tool_use_blocks = [
                    b for b in final_msg.content
                    if hasattr(b, "type") and b.type == "tool_use"
                ]
                if tool_use_blocks and tool_executor:
                    log.info("Executing %d Anthropic tool(s) for %s", len(tool_use_blocks), persona_key)
                    # Rebuild messages with assistant tool_use + user tool_result
                    anth_messages.append({"role": "assistant", "content": final_msg.content})
                    tool_results = []
                    for tb in tool_use_blocks:
                        try:
                            args = tb.input if isinstance(tb.input, dict) else {}
                            log.info("  → %s(%s)", tb.name, args)
                            result = tool_executor(tb.name, args)
                        except Exception as exc:
                            log.error("Tool %s failed: %s", tb.name, exc)
                            result = f"Error: {exc}"
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": tb.id,
                            "content": str(result),
                        })
                    anth_messages.append({"role": "user", "content": tool_results})
                    full_response.clear()  # second pass is the real response
                    with chat_client.messages.stream(
                        model=selected_model,
                        max_tokens=4096,
                        temperature=temp,
                        system=anth_system,
                        messages=anth_messages,
                    ) as stream2:
                        for text in stream2.text_stream:
                            full_response.append(text)
                            yield f"data: {json.dumps({'token': text})}\n\n"

            else:
                # OpenAI-compatible path (fallback)
                kwargs: Dict[str, Any] = dict(
                    model=selected_model,
                    temperature=temp,
                    max_tokens=4096,
                    stream=True,
                    messages=llm_messages,
                )
                if tool_schemas:
                    kwargs["tools"] = tool_schemas
                    kwargs["tool_choice"] = "auto"

                stream = chat_client.chat.completions.create(**kwargs)
                tool_calls_acc: Dict[int, Dict[str, str]] = {}

                for chunk in stream:
                    choice = chunk.choices[0]
                    delta = choice.delta
                    if delta.tool_calls:
                        for tc in delta.tool_calls:
                            idx = tc.index
                            if idx not in tool_calls_acc:
                                tool_calls_acc[idx] = {"id": tc.id or "", "name": "", "arguments": ""}
                            if tc.id: tool_calls_acc[idx]["id"] = tc.id
                            if tc.function:
                                if tc.function.name: tool_calls_acc[idx]["name"] += tc.function.name
                                if tc.function.arguments: tool_calls_acc[idx]["arguments"] += tc.function.arguments
                        continue
                    content = delta.content or ""
                    if content:
                        full_response.append(content)
                        yield f"data: {json.dumps({'token': content})}\n\n"

                if tool_calls_acc and tool_executor:
                    log.info("Executing %d OpenAI tool(s) for %s", len(tool_calls_acc), persona_key)
                    assistant_tool_msg: Dict[str, Any] = {
                        "role": "assistant",
                        "tool_calls": [
                            {"id": tc["id"], "type": "function",
                             "function": {"name": tc["name"], "arguments": tc["arguments"]}}
                            for tc in tool_calls_acc.values()
                        ],
                    }
                    llm_messages.append(assistant_tool_msg)
                    for tc in tool_calls_acc.values():
                        try:
                            args = json.loads(tc["arguments"]) if tc["arguments"] else {}
                            log.info("  → %s(%s)", tc["name"], args)
                            result = tool_executor(tc["name"], args)
                        except Exception as exc:
                            log.error("Tool %s failed: %s", tc["name"], exc)
                            result = f"Error executing {tc['name']}: {exc}"
                        llm_messages.append({"role": "tool", "tool_call_id": tc["id"], "content": str(result)})
                    stream2 = chat_client.chat.completions.create(
                        model=selected_model,
                        temperature=temp,
                        max_tokens=4096,
                        stream=True,
                        messages=llm_messages,
                    )
                    for chunk in stream2:
                        content = chunk.choices[0].delta.content or ""
                        if content:
                            full_response.append(content)
                            yield f"data: {json.dumps({'token': content})}\n\n"

            latency = (time.perf_counter() - t0) * 1000
            log.info(
                "[%s] Response complete — %.0fms, %d chars",
                persona_key,
                latency,
                sum(len(t) for t in full_response),
            )

        except Exception as exc:
            error_text = f"[Error: {exc}]"
            log.error("LLM stream error: %s", exc, exc_info=True)
            full_response.append(error_text)
            yield f"data: {json.dumps({'token': error_text})}\n\n"

        # Save complete assistant message to DB
        complete = "".join(full_response)
        if complete:
            with app.app_context():
                assistant_msg = ChatMessage(
                    session_id=session_id,
                    role="assistant",
                    content=complete,
                )
                db.session.add(assistant_msg)
                session.updated_at = datetime.now(timezone.utc)
                db.session.commit()
                yield f"data: {json.dumps({'done': True, 'message_id': assistant_msg.id})}\n\n"

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# ---------------------------------------------------------------------------
# API — Voice (in-process pipeline preferred, proxy fallback)
# ---------------------------------------------------------------------------
KIRO_SERVER_URL = os.environ.get("KIRO_SERVER_URL", "http://127.0.0.1:5400")


def _voice_via_pipeline(wav_data: bytes, session_id: str, persona_key: str) -> dict:
    """Process voice through the in-process VoicePipeline (no network hop)."""
    pipeline = app.config.get("voice_pipeline")
    if pipeline is None:
        return None  # signal caller to fall back to proxy
    result = pipeline.process(wav_data, session_id=session_id, persona=persona_key)
    audio_b64 = base64.b64encode(result["audio"]).decode("ascii") if result.get("audio") else ""
    return {
        "transcript": result.get("transcript", ""),
        "response_text": result.get("response_text", ""),
        "persona": result.get("persona", persona_key),
        "audio_b64": audio_b64,
        "timing": result.get("timing", {}),
        "session_id": result.get("session_id", session_id),
    }


def _voice_via_proxy(wav_data: bytes, session_id: str, persona_key: str) -> dict:
    """Proxy voice to a remote kiro_server instance (legacy / --no-voice mode)."""
    req = urllib.request.Request(
        f"{KIRO_SERVER_URL}/process",
        data=wav_data,
        headers={
            "Content-Type": "audio/wav",
            "X-Session-Id": session_id,
            "X-Persona": persona_key,
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        resp_data = resp.read()
        return {
            "transcript": resp.headers.get("X-Transcript", ""),
            "response_text": resp.headers.get("X-Response-Text", ""),
            "persona": resp.headers.get("X-Persona", persona_key),
            "audio_b64": base64.b64encode(resp_data).decode("ascii"),
            "timing": json.loads(resp.headers.get("X-Timing", "{}")),
            "session_id": resp.headers.get("X-Session-Id", session_id),
        }


@app.route("/api/voice", methods=["POST"])
def api_voice():
    """
    Voice pipeline endpoint.

    Tries the in-process VoicePipeline first (zero-latency, loaded via
    kiro_command.py --with-voice).  Falls back to proxying to a standalone
    kiro_server if the pipeline isn't loaded.
    """
    wav_data = request.get_data()
    if not wav_data:
        return jsonify({"error": "No audio data"}), 400

    session_id = request.headers.get("X-Session-Id", "")
    persona_key = request.headers.get("X-Persona", "kiro")

    try:
        # ---- Try in-process first ----
        result = _voice_via_pipeline(wav_data, session_id, persona_key)
        if result is None:
            # ---- Fall back to proxy ----
            result = _voice_via_proxy(wav_data, session_id, persona_key)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        log.error("kiro_server returned %d: %s", e.code, body)
        return jsonify({"error": f"Voice server error ({e.code})", "detail": body}), 502
    except urllib.error.URLError as e:
        log.error("Cannot reach kiro_server: %s", e.reason)
        return jsonify({
            "error": "Voice server unreachable",
            "detail": f"kiro_server at {KIRO_SERVER_URL} is not running. Start it first.",
        }), 503
    except Exception as e:
        log.error("Voice processing error: %s", e, exc_info=True)
        return jsonify({"error": f"Voice processing error: {e}"}), 500

    transcript = result.get("transcript", "")
    response_text = result.get("response_text", "")

    # Save messages to chat DB if we have a session_id
    if session_id:
        try:
            session = ChatSession.query.get(int(session_id))
            if session:
                if transcript:
                    user_msg = ChatMessage(
                        session_id=session.id, role="user", content=f"🎤 {transcript}"
                    )
                    db.session.add(user_msg)
                if response_text:
                    asst_msg = ChatMessage(
                        session_id=session.id, role="assistant", content=response_text
                    )
                    db.session.add(asst_msg)
                if transcript or response_text:
                    session.updated_at = datetime.now(timezone.utc)
                    if session.title == "New Session" and transcript:
                        session.title = transcript[:100]
                    db.session.commit()
        except Exception as e:
            log.warning("Failed to save voice messages to DB: %s", e)

    return jsonify(result)


@app.route("/api/voice/health")
def api_voice_health():
    """Check voice pipeline status."""
    pipeline = app.config.get("voice_pipeline")
    if pipeline is not None:
        return jsonify({"available": True, "mode": "in-process"})
    # Fall back — check remote kiro_server
    try:
        req = urllib.request.Request(f"{KIRO_SERVER_URL}/health", method="GET")
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
            return jsonify({"available": True, "mode": "proxy", "server": data})
    except Exception:
        return jsonify({"available": False, "mode": "none", "server_url": KIRO_SERVER_URL})


# ---------------------------------------------------------------------------
# API — Finley sync
# ---------------------------------------------------------------------------

@app.route("/api/finley/sync", methods=["POST"])
def api_finley_sync():
    """
    Trigger an immediate YNAB sync in the background.
    Returns quickly — sync runs in the existing daemon thread.
    """
    if not _finley_available:
        return jsonify({"error": "Finley module not available"}), 503
    if _finley_sync_daemon is None:
        return jsonify({"error": "Sync daemon not running"}), 503
    # Run a one-shot sync in a fresh thread so this request doesn't block
    import threading
    from finley.sync import run_sync_once
    def _do_sync():
        try:
            results = run_sync_once(post_sync_callback=finley_post_sync)
            log.info("Manual Finley sync complete: %s", results)
        except Exception as exc:
            log.error("Manual Finley sync failed: %s", exc, exc_info=True)
    threading.Thread(target=_do_sync, name="finley-manual-sync", daemon=True).start()
    return jsonify({"ok": True, "message": "Sync started in background"})


@app.route("/api/finley/sync/status")
def api_finley_sync_status():
    """Return the last sync result, whether the daemon is running, and data freshness."""
    if not _finley_available or _finley_sync_daemon is None:
        return jsonify({"available": False})
    from finley.config import load_config
    from finley.db import FinleyDB
    cfg = load_config()
    last_sk = cfg.get("last_server_knowledge", {})
    # Pull latest transaction date and last sync timestamp from DB
    latest_txn_date = None
    last_sync_at = None
    try:
        fdb = FinleyDB()
        conn = fdb._conn()
        with conn.cursor() as cur:
            cur.execute("SELECT MAX(date) FROM finley_transactions")
            row = cur.fetchone()
            latest_txn_date = str(row[0]) if row and row[0] else None
            cur.execute("SELECT MAX(synced_at) FROM finley_sync_log")
            row = cur.fetchone()
            last_sync_at = str(row[0]) if row and row[0] else None
        conn.commit()
        fdb._put(conn)
    except Exception:
        pass
    return jsonify({
        "available": True,
        "daemon_running": _finley_sync_daemon.is_running,
        "last_result": _finley_sync_daemon.last_result,
        "last_server_knowledge": last_sk,
        "latest_transaction_date": latest_txn_date,
        "last_sync_at": last_sync_at,
        "note": "transactions:0 means YNAB has no new data — check your YNAB bank connection",
    })


# ---------------------------------------------------------------------------
# API — Proactive Catchup
# ---------------------------------------------------------------------------

_CATCHUP_SYSTEM_PROMPT = """You are an AI assistant delivering a proactive {period} briefing to Tim.{persona_context}

Your voice: warm, direct, efficient. Like a trusted advisor who respects Tim's time. Not robotic, not chatty. Slightly witty when appropriate.

Rules:
- Open with a natural greeting appropriate to the time of day
- Lead with the most important or time-sensitive items
- Group related items naturally
- Keep it concise — this is a quick check-in, not a lecture
- Close with anything Tim should keep in mind or act on
- If there's very little to report, keep it SHORT — don't pad
- Use markdown formatting (bold for emphasis, lists for multiple items)
- Current date/time: {timestamp} (Vancouver, BC)
"""

_CATCHUP_USER_TEMPLATE = """Here is the context gathered from Tim's systems:

{context_block}

Compose a concise {period} catchup for Tim. Be natural and direct."""

# ---------------------------------------------------------------------------
# Per-persona rules for catchup context assembly
# ---------------------------------------------------------------------------
_CATCHUP_PERSONA_CONFIG: Dict[str, Dict] = {
    "finley": {
        "name": "Finley",
        "include_weather":  False,
        "include_finley":   True,
        "include_calendar": True,   # payment deadlines, financial appointments
        "include_email":    True,   # billing notices, bank alerts
        "persona_context": (
            "\n\nYou are delivering this briefing AS Finley, Tim's financial advisor. "
            "Focus exclusively on financial matters — budget status, spending trends, "
            "upcoming bills, and account activity. Include calendar or email ONLY if "
            "they have direct financial implications (e.g., payment due, billing notice, "
            "tax deadline). Do NOT mention weather, gardening, fitness, or any topic "
            "outside Tim's finances."
        ),
    },
    "jack": {
        "name": "Jack",
        "include_weather":  True,   # critical for cultivation
        "include_finley":   False,
        "include_calendar": True,   # grow tasks, feeding schedules
        "include_email":    False,
        "persona_context": (
            "\n\nYou are delivering this briefing AS Jack, Tim's cultivation advisor. "
            "Focus on plant health, growing conditions, and environmental data. "
            "Weather IS highly relevant here. Do NOT include financial data or other "
            "non-cultivation topics."
        ),
    },
    "coach": {
        "name": "Coach",
        "include_weather":  False,
        "include_finley":   False,
        "include_calendar": True,
        "include_email":    True,
        "persona_context": (
            "\n\nYou are delivering this briefing AS Coach, Tim's executive function "
            "assistant. Focus on tasks, upcoming deadlines, habits, and goals. Include "
            "calendar and email for action items and commitments. Skip financial data "
            "and gardening tips unless they relate to a goal Tim is actively tracking."
        ),
    },
    "kiro": {
        "name": "Kiro",
        "include_weather":  True,
        "include_finley":   True,
        "include_calendar": True,
        "include_email":    True,
        "persona_context": (
            "\n\nYou are Kiro, Tim's AI chief of staff. Deliver a comprehensive "
            "cross-system briefing. Cross-reference where relevant — if weather affects "
            "plans, if a calendar event has financial implications, surface those "
            "connections."
        ),
    },
}


@app.route("/api/catchup/check")
def api_catchup_check():
    """
    Lightweight check: should we surface a proactive catchup?

    Returns {available: true/false, period, label}.
    The frontend polls this every ~60s when the tab is visible.
    """
    now = datetime.now()
    hour = now.hour

    # Only during reasonable hours (7am–10pm)
    if hour < 7 or hour >= 22:
        return jsonify({"available": False})

    # Cooldown: don't re-offer if we already delivered recently
    last = _catchup_state["last_completed_at"]
    if last:
        elapsed_h = (now - last).total_seconds() / 3600
        if elapsed_h < _catchup_state["cooldown_hours"]:
            return jsonify({"available": False})

    # Determine period label
    if hour < 12:
        period, label = "morning", "Morning catchup"
    elif hour < 17:
        period, label = "afternoon", "Afternoon check-in"
    else:
        period, label = "evening", "Evening wrap-up"

    _catchup_state["last_offered_at"] = now

    return jsonify({"available": True, "period": period, "label": label})


@app.route("/api/catchup/dismiss", methods=["POST"])
def api_catchup_dismiss():
    """Dismiss the catchup banner without viewing it. Resets cooldown."""
    _catchup_state["last_completed_at"] = datetime.now()
    return jsonify({"ok": True})


@app.route("/api/catchup", methods=["POST"])
def api_catchup():
    """
    Generate and stream a persona-aware proactive catchup.

    The active persona drives which data sources are included — Finley gets
    a finance-only briefing, Jack gets cultivation + weather, Kiro gets
    the full cross-system view.  Streamed as SSE, same format as /api/chat.
    """
    chat_client, provider = _get_chat_client()
    if not chat_client:
        return jsonify({"error": "No LLM client available"}), 503

    data = request.get_json() or {}
    session_id = data.get("session_id")
    persona_key = (data.get("persona_key") or "kiro").lower()
    persona_cfg = _CATCHUP_PERSONA_CONFIG.get(persona_key, _CATCHUP_PERSONA_CONFIG["kiro"])

    # Mark catchup as delivered (reset cooldown)
    _catchup_state["last_completed_at"] = datetime.now()

    now = datetime.now()
    hour = now.hour
    if hour < 12:
        period = "morning"
    elif hour < 17:
        period = "afternoon"
    else:
        period = "evening"

    def generate():
        full_response: List[str] = []
        t0 = time.perf_counter()

        try:
            # Gather context — insights + chats already filtered to persona
            ctx = _gather_catchup_context(persona_key=persona_key)

            # Build context block — per-persona source gating
            sections = []

            if ctx["weather"] and persona_cfg["include_weather"]:
                sections.append(f"**Weather:**\n{ctx['weather']}")

            if ctx["calendar"] and persona_cfg["include_calendar"]:
                sections.append(f"**Calendar:**\n{ctx['calendar']}")

            if ctx["email"] and persona_cfg["include_email"]:
                sections.append(f"**Email:**\n{ctx['email']}")

            if ctx["insights"]:
                insight_lines = [
                    f"- [{ins['type']}] (p{ins['priority']}): {ins['summary']}"
                    for ins in ctx["insights"]
                ]
                sections.append("**Insights:**\n" + "\n".join(insight_lines))

            if ctx["finley"] and persona_cfg["include_finley"]:
                sections.append(f"**Finance:**\n{ctx['finley']}")

            if ctx["recent_chats"]:
                chat_lines = [
                    f"- \"{ch['title']}\" — last: {ch['preview'][:120]}..."
                    for ch in ctx["recent_chats"]
                ]
                sections.append("**Recent Conversations:**\n" + "\n".join(chat_lines))

            if not sections:
                sections.append(
                    "No data sources returned content. "
                    "Just greet Tim briefly and let him know things look quiet."
                )

            context_block = "\n\n".join(sections)

            system_prompt = _CATCHUP_SYSTEM_PROMPT.format(
                period=period,
                timestamp=now.strftime("%A, %B %d %Y, %I:%M %p"),
                persona_context=persona_cfg["persona_context"],
            )
            user_prompt = _CATCHUP_USER_TEMPLATE.format(
                period=period,
                context_block=context_block,
            )

            catchup_model = _get_model_for_tier("balanced")
            temp = MODEL_ROUTING.get("temperature", 0.5)

            if provider == "anthropic":
                with chat_client.messages.stream(
                    model=catchup_model,
                    max_tokens=4096,
                    temperature=temp,
                    system=system_prompt,
                    messages=[{"role": "user", "content": user_prompt}],
                ) as stream:
                    for text in stream.text_stream:
                        full_response.append(text)
                        yield f"data: {json.dumps({'token': text})}\n\n"
            else:
                stream = chat_client.chat.completions.create(
                    model=catchup_model,
                    temperature=temp,
                    max_tokens=4096,
                    stream=True,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                )
                for chunk in stream:
                    content = chunk.choices[0].delta.content or ""
                    if content:
                        full_response.append(content)
                        yield f"data: {json.dumps({'token': content})}\n\n"

            latency = (time.perf_counter() - t0) * 1000
            log.info("[catchup] %s briefing — %.0fms, %d chars",
                     period, latency, sum(len(t) for t in full_response))

        except Exception as exc:
            error_text = f"[Catchup error: {exc}]"
            log.error("Catchup generation failed: %s", exc, exc_info=True)
            full_response.append(error_text)
            yield f"data: {json.dumps({'token': error_text})}\n\n"

        # Save to DB if we have a session
        complete = "".join(full_response)
        if complete and session_id:
            try:
                with app.app_context():
                    assistant_msg = ChatMessage(
                        session_id=session_id,
                        role="assistant",
                        content=complete,
                    )
                    db.session.add(assistant_msg)
                    session = ChatSession.query.get(session_id)
                    if session:
                        session.updated_at = datetime.now(timezone.utc)
                    db.session.commit()
            except Exception as e:
                log.warning("Failed to save catchup message: %s", e)

        yield f"data: {json.dumps({'done': True})}\n\n"

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# ---------------------------------------------------------------------------
# Run (used by launcher or standalone)
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    # use_reloader=False prevents Werkzeug from forking a second process
    # that would start a duplicate Finley sync daemon.
    app.run(host="0.0.0.0", port=FLASK_PORT, debug=False, use_reloader=False)
