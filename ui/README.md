# KIRO UI

Desktop chat interface for the Kiro persona system. Native window via pywebview, Flask backend, PostgreSQL storage.

## Architecture

```
┌──────────┐     ┌────────────┐     ┌────────────┐     ┌──────────────┐
│ pywebview │────▶│   Flask    │────▶│ PostgreSQL │     │  OpenRouter  │
│  (GTK3)  │◀────│  :5199     │◀────│  (kiro db) │     │  / any LLM   │
└──────────┘     └────────────┘     └────────────┘     └──────────────┘
                       │                                       ▲
                       └───────── streaming SSE ───────────────┘
```

**Key decisions:**
- Same PostgreSQL instance as Kiro core — separate tables (`kiro_chat_sessions`, `kiro_chat_messages`)
- Config-over-code: add a persona in `config.py` and it appears in the UI
- LLM-agnostic: any OpenAI-compatible API endpoint (OpenRouter default)
- Portable: override DB/LLM settings via env vars for other machines

## Setup

```bash
# From the kiro-ui directory
pip install -r requirements.txt

# pywebview needs GTK3 on Ubuntu
sudo apt install python3-gi python3-gi-cairo gir1.2-gtk-3.0 gir1.2-webkit2-4.1

# Set your LLM API key
export KIRO_LLM_KEY="your-openrouter-key"

# Optional: override defaults
export KIRO_LLM_MODEL="anthropic/claude-sonnet-4"
export KIRO_DB_NAME="kiro"
export KIRO_DB_USER="kiro"
export KIRO_DB_PASS="kiro"
```

## Running

```bash
# Native desktop window
python launcher.py

# Debug mode (browser at http://127.0.0.1:5199)
python launcher.py --debug

# Flask only (no native window)
python app.py
```

## Keyboard Shortcuts

| Key           | Action              |
|---------------|---------------------|
| Ctrl+1–7      | Switch persona      |
| Ctrl+N        | New session         |
| Enter         | Send message        |
| Shift+Enter   | Newline in input    |
| Escape        | Focus input         |

## Adding a Persona

Edit `config.py` → `PERSONAS` dict. Add the key to `PERSONA_ORDER`. That's it.

```python
PERSONAS = {
    "newpersona": {
        "name": "NAME",
        "full_name": "Display Name",
        "role": "Role Description",
        "accent": "#HEX",
        "accent_dim": "#HEX",
        "avatar": "🎭",
        "greeting": "First message shown to user.",
        "system_prompt": "LLM system prompt...",
    },
}
```

## Environment Variables

| Variable          | Default                              | Description              |
|-------------------|--------------------------------------|--------------------------|
| KIRO_DB_HOST      | localhost                            | PostgreSQL host          |
| KIRO_DB_PORT      | 5432                                 | PostgreSQL port          |
| KIRO_DB_NAME      | kiro                                 | Database name            |
| KIRO_DB_USER      | kiro                                 | Database user            |
| KIRO_DB_PASS      | kiro                                 | Database password        |
| KIRO_DATABASE_URL | (constructed from above)             | Full override            |
| KIRO_LLM_URL      | https://openrouter.ai/api/v1/...     | LLM endpoint             |
| KIRO_LLM_KEY      | (none)                               | API key                  |
| KIRO_LLM_MODEL    | anthropic/claude-sonnet-4            | Model identifier         |
| KIRO_UI_PORT      | 5199                                 | Flask port               |
| KIRO_SECRET       | kiro-dev-key-change-me               | Flask secret key         |
