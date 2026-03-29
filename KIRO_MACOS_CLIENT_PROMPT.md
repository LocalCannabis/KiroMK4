# Copilot Prompt: Kiro macOS Client Package + Secure Beast Connection

## Context

I have an existing **Kiro multi-persona AI assistant** running on a home server called "the Beast" (Ubuntu 22.04, Flask/PostgreSQL). The web frontend is a **text-based overlay interface with tabbed persona switching** (Jinja2 templates, Tailwind CSS, vanilla JS). Each tab is a separate persona (Kiro, Finley, Coach, Chef, Doc, Sage, Jack) with its own conversation thread.

All conversation state, LLM inference, TTS, and memory live on the Beast. The frontend is purely a thin client.

I need to **package this as a deployable macOS desktop app** that I can run on my work iMac, connecting securely back to the Beast over Tailscale. Conversations must be seamless — I pick up on the Mac exactly where I left off at home.

## Constraints

- **Python-first.** No React, no Vue, no Electron. Use `pywebview` to wrap the existing web UI in a native macOS window.
- **All state lives on the Beast.** The Mac client stores nothing except connection config and an auth token. Zero conversation data at rest on the work machine.
- **Tailscale is already running** on both machines and provides the encrypted tunnel. The Beast has a stable Tailscale IP (e.g., `100.x.x.x`). Do not reinvent the tunnel — use what Tailscale gives us.
- **Auth must be real.** Not security-through-obscurity. Token-based authentication on every request.
- **Package as a standalone .app** using `py2app` so it runs on the iMac without requiring a Python environment.
- **macOS-native feel.** Dock icon + menubar icon, both always available. Menubar provides status, persona switching, show/hide, and a runtime dock toggle. `Cmd+Q` to quit. Dark mode follows system. Minimal footprint.

---

## Part 1: Beast-Side Setup (Flask)

### 1A. HTTPS via Tailscale

Tailscale can provision TLS certs for machines on the tailnet.

```bash
# On the Beast
sudo tailscale cert beast.tail-XXXXX.ts.net
```

Update the Kiro Flask app (or its reverse proxy) to serve over HTTPS using the Tailscale-provisioned cert. If running behind nginx or caddy on the Beast, configure the cert there. If serving Flask directly for dev, use:

```python
app.run(
    host='0.0.0.0',
    port=5050,
    ssl_context=(
        '/path/to/beast.tail-XXXXX.ts.net.crt',
        '/path/to/beast.tail-XXXXX.ts.net.key'
    )
)
```

### 1B. Token Authentication

Create a simple bearer token auth system. No user accounts needed — this is single-user.

```
Table: kiro_api_tokens
  id SERIAL PRIMARY KEY
  token_hash TEXT NOT NULL          -- bcrypt hash of the token
  label TEXT                        -- e.g. "work-imac"
  created_at TIMESTAMP DEFAULT NOW()
  last_used_at TIMESTAMP
  revoked BOOLEAN DEFAULT FALSE
```

Flask middleware:

```python
from functools import wraps
from flask import request, jsonify
import bcrypt

def require_kiro_token(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        auth_header = request.headers.get('Authorization', '')
        if not auth_header.startswith('Bearer '):
            return jsonify({'error': 'unauthorized'}), 401

        token = auth_header.split(' ', 1)[1]
        # Check against stored hashes
        valid = validate_token(token)  # query kiro_api_tokens, bcrypt.checkpw()
        if not valid:
            return jsonify({'error': 'unauthorized'}), 401

        return f(*args, **kwargs)
    return decorated
```

Apply `@require_kiro_token` to all Kiro blueprint routes. Generate tokens via CLI:

```python
# scripts/generate_kiro_token.py
import secrets, bcrypt
token = secrets.token_urlsafe(32)
token_hash = bcrypt.hashpw(token.encode(), bcrypt.gensalt()).decode()
print(f"Token (save this, shown once): {token}")
print(f"Hash (store in DB): {token_hash}")
```

### 1C. Conversation Continuity

Conversations are already in PostgreSQL. The Mac client is just another browser hitting the same Flask routes. Ensure:

- Session state is **not** cookie-based (or if it is, that cookies work cross-device with the token auth as primary).
- The active persona tab and scroll position can be restored via a simple `/api/session/state` endpoint:

```json
GET /api/session/state
{
  "active_persona": "coach",
  "personas": {
    "coach": { "last_message_id": 1482, "has_unread": false },
    "finley": { "last_message_id": 971, "has_unread": true }
  }
}
```

This lets the Mac client open to exactly where things left off.

---

## Part 2: macOS Client (pywebview + py2app)

### 2A. Project Structure

```
kiro-mac-client/
├── kiro_client/
│   ├── __init__.py
│   ├── app.py              # Main entry point
│   ├── config.py            # Connection settings loader
│   ├── keychain.py          # macOS Keychain integration
│   ├── connection.py        # Health check / reconnect logic
│   ├── proxy.py             # Local auth proxy (Flask :5051)
│   ├── menubar.py           # rumps menubar integration
│   ├── dock.py              # Dock icon show/hide via AppKit
│   └── resources/
│       ├── icon.icns             # App icon (Kiro logo)
│       ├── menubar_icon.png      # 22x22 template image (normal)
│       └── menubar_icon_err.png  # 22x22 template image (disconnected)
├── config.yaml              # User config (Beast URL, no secrets)
├── setup.py                 # py2app configuration
└── requirements.txt
```

### 2B. config.yaml

```yaml
beast:
  host: "beast.tail-XXXXX.ts.net"
  port: 5050
  protocol: "https"

client:
  window_title: "KIRO"
  width: 480
  height: 720
  on_top: true               # Overlay behavior — stays on top
  start_minimized: false
  mode: "both"               # "dock", "menubar", or "both"
```

The auth token is NOT in this file. It goes in the macOS Keychain.

### 2C. Keychain Integration

Use `keyring` (Python lib that wraps macOS Keychain natively):

```python
import keyring

SERVICE_NAME = "kiro-client"
ACCOUNT_NAME = "beast-api-token"

def store_token(token: str):
    keyring.set_password(SERVICE_NAME, ACCOUNT_NAME, token)

def get_token() -> str | None:
    return keyring.get_password(SERVICE_NAME, ACCOUNT_NAME)

def delete_token():
    keyring.delete_password(SERVICE_NAME, ACCOUNT_NAME)
```

First-run flow: if no token in Keychain, show a simple input dialog (pywebview dialog or native prompt) asking for the token. Store it. Never ask again unless revoked.

### 2D. Main App (pywebview)

```python
import webview
import yaml
import threading
from kiro_client.keychain import get_token, store_token
from kiro_client.connection import check_beast_connection
from kiro_client.menubar import start_menubar

def get_config():
    with open('config.yaml', 'r') as f:
        return yaml.safe_load(f)

class KiroAPI:
    """Exposed to JS in the webview for native interactions."""
    def get_connection_status(self):
        return check_beast_connection()

def main():
    config = get_config()
    token = get_token()

    if not token:
        # First-run: prompt for token
        token = prompt_for_token()  # pywebview dialog
        store_token(token)

    beast_url = (
        f"{config['beast']['protocol']}://"
        f"{config['beast']['host']}:{config['beast']['port']}"
        f"/kiro/overlay"
    )

    # Start the local auth proxy in a background thread
    from kiro_client.proxy import start_proxy
    proxy_thread = threading.Thread(target=start_proxy, daemon=True)
    proxy_thread.start()

    api = KiroAPI()

    window = webview.create_window(
        title=config['client']['window_title'],
        url=f"http://127.0.0.1:5051/",   # Local proxy
        width=config['client']['width'],
        height=config['client']['height'],
        on_top=config['client']['on_top'],
        js_api=api,
        background_color='#0f172a'        # Tailwind slate-900
    )

    # Start menubar icon in background thread (always runs)
    menubar_thread = threading.Thread(
        target=start_menubar,
        args=(window,),
        daemon=True
    )
    menubar_thread.start()

    webview.start(debug=False)

if __name__ == '__main__':
    main()
```

### 2D-ii. Menubar Integration (rumps)

Use `rumps` — a lightweight Python lib for macOS menubar apps. This gives you a persistent status icon with quick actions regardless of whether the dock icon is showing.

```python
# kiro_client/menubar.py
import rumps
from kiro_client.connection import check_beast_connection

class KiroMenubar(rumps.App):
    """Persistent menubar icon with quick actions."""

    def __init__(self, webview_window):
        super().__init__(
            name="KIRO",
            icon="kiro_client/resources/menubar_icon.png",  # 22x22 template image
            quit_button=None  # We handle quit ourselves
        )
        self.window = webview_window
        self.status_item = rumps.MenuItem("Status: checking...")
        self.menu = [
            self.status_item,
            None,  # separator
            rumps.MenuItem("Show KIRO", callback=self.show_window),
            rumps.MenuItem("Hide KIRO", callback=self.hide_window),
            None,
            rumps.MenuItem("Personas", [
                rumps.MenuItem("Kiro", callback=lambda _: self.switch_persona("kiro")),
                rumps.MenuItem("Coach", callback=lambda _: self.switch_persona("coach")),
                rumps.MenuItem("Finley", callback=lambda _: self.switch_persona("finley")),
                rumps.MenuItem("Chef", callback=lambda _: self.switch_persona("chef")),
                rumps.MenuItem("Doc", callback=lambda _: self.switch_persona("doc")),
                rumps.MenuItem("Sage", callback=lambda _: self.switch_persona("sage")),
                rumps.MenuItem("Jack", callback=lambda _: self.switch_persona("jack")),
            ]),
            None,
            rumps.MenuItem("Quit KIRO", callback=self.quit_app),
        ]

    @rumps.timer(30)
    def update_status(self, _):
        """Ping Beast every 30s, update menubar icon state."""
        status = check_beast_connection()
        if status.get('connected'):
            latency = status.get('latency_ms', '?')
            self.status_item.title = f"Connected ({latency}ms)"
            self.icon = "kiro_client/resources/menubar_icon.png"       # normal
        else:
            error = status.get('error', 'unknown')
            self.status_item.title = f"Disconnected: {error}"
            self.icon = "kiro_client/resources/menubar_icon_err.png"   # red variant

    def show_window(self, _):
        if self.window:
            self.window.show()
            self.window.on_top = True

    def hide_window(self, _):
        if self.window:
            self.window.hide()

    def switch_persona(self, persona_slug):
        """Switch active tab via JS injection."""
        if self.window:
            self.window.show()
            self.window.evaluate_js(f"switchPersona('{persona_slug}')")

    def quit_app(self, _):
        rumps.quit_application()

def start_menubar(webview_window):
    KiroMenubar(webview_window).run()
```

### 2D-iii. Dock Icon Mode Toggle

The `mode` setting in `config.yaml` controls visibility:

- **`"both"`** (default) — Dock icon + menubar icon. Full macOS citizen. Cmd+Tab to KIRO, or click the menubar for quick actions.
- **`"menubar"`** — Menubar only. No dock icon. True overlay. Set `LSUIElement: True` in the plist at build time, or toggle at runtime:

```python
# kiro_client/dock.py
import AppKit

def set_dock_visibility(show: bool):
    """Show or hide the dock icon at runtime."""
    app = AppKit.NSApplication.sharedApplication()
    if show:
        app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyRegular)
    else:
        app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)
```

Wire this into startup in `app.py`:

```python
from kiro_client.dock import set_dock_visibility

mode = config['client'].get('mode', 'both')
if mode == 'menubar':
    set_dock_visibility(False)
elif mode == 'dock':
    set_dock_visibility(True)
    # Still run menubar thread — it's useful regardless
else:  # "both"
    set_dock_visibility(True)
```

Add a toggle to the menubar menu so you can switch on the fly:

```python
# Inside KiroMenubar.__init__
self.dock_toggle = rumps.MenuItem("Hide Dock Icon", callback=self.toggle_dock)
# Insert into self.menu before Quit

def toggle_dock(self, sender):
    from kiro_client.dock import set_dock_visibility
    if sender.title == "Hide Dock Icon":
        set_dock_visibility(False)
        sender.title = "Show Dock Icon"
    else:
        set_dock_visibility(True)
        sender.title = "Hide Dock Icon"
```

### 2E. Local Auth Proxy (tiny Flask app on the client)

This solves the "pywebview can't set custom headers on page load" problem. A ~30-line Flask app running on localhost that proxies all requests to the Beast with the Bearer token injected.

```python
from flask import Flask, request, Response
import requests as http_requests
from kiro_client.keychain import get_token
from kiro_client.config import get_config

proxy = Flask(__name__)
config = get_config()
BEAST_BASE = f"{config['beast']['protocol']}://{config['beast']['host']}:{config['beast']['port']}"

@proxy.route('/', defaults={'path': ''})
@proxy.route('/<path:path>', methods=['GET', 'POST', 'PUT', 'DELETE'])
def proxy_to_beast(path):
    token = get_token()
    target_url = f"{BEAST_BASE}/kiro/overlay/{path}"

    headers = {k: v for k, v in request.headers if k.lower() != 'host'}
    headers['Authorization'] = f'Bearer {token}'

    resp = http_requests.request(
        method=request.method,
        url=target_url,
        headers=headers,
        data=request.get_data(),
        params=request.args,
        allow_redirects=False,
        verify=True                # Validates Tailscale TLS cert
    )

    return Response(
        resp.content,
        status=resp.status_code,
        headers=dict(resp.headers)
    )

def start_proxy():
    proxy.run(host='127.0.0.1', port=5051, threaded=True)
```

Start the proxy in a background thread before launching pywebview.

### 2F. Connection Health + Reconnect

```python
import requests

def check_beast_connection() -> dict:
    """Ping Beast health endpoint."""
    config = get_config()
    token = get_token()
    try:
        r = requests.get(
            f"{BEAST_BASE}/kiro/health",
            headers={'Authorization': f'Bearer {token}'},
            timeout=5
        )
        return {'connected': r.status_code == 200, 'latency_ms': r.elapsed.microseconds // 1000}
    except requests.ConnectionError:
        return {'connected': False, 'error': 'tailscale_unreachable'}
    except requests.Timeout:
        return {'connected': False, 'error': 'timeout'}
```

Surface this in the UI: small dot in the tab bar (green = connected, amber = slow, red = disconnected). On disconnect, show a non-blocking banner: "Beast unreachable — check Tailscale" with a retry button.

### 2G. py2app Packaging

```python
# setup.py
from setuptools import setup

APP = ['kiro_client/app.py']
DATA_FILES = ['config.yaml']
OPTIONS = {
    'argv_emulation': False,
    'iconfile': 'kiro_client/resources/icon.icns',
    'plist': {
        'CFBundleName': 'KIRO',
        'CFBundleDisplayName': 'KIRO',
        'CFBundleIdentifier': 'com.kiro.client',
        'CFBundleVersion': '1.0.0',
        'LSUIElement': False,          # Always False — dock visibility toggled at runtime via AppKit
        'NSAppTransportSecurity': {
            'NSAllowsLocalNetworking': True
        }
    },
    'packages': ['webview', 'flask', 'requests', 'keyring', 'yaml', 'rumps'],
    'resources': [
        'kiro_client/resources/menubar_icon.png',
        'kiro_client/resources/menubar_icon_err.png',
    ],
}

setup(
    app=APP,
    data_files=DATA_FILES,
    options={'py2app': OPTIONS},
    setup_requires=['py2app'],
)
```

Build:
```bash
python setup.py py2app
# Output: dist/KIRO.app
```

---

## Part 3: Deployment Checklist

### Beast Side
- [ ] Generate Tailscale TLS cert (`tailscale cert`)
- [ ] Configure Flask/nginx to serve Kiro on HTTPS :5050
- [ ] Create `kiro_api_tokens` table
- [ ] Generate token for work iMac, store hash in DB
- [ ] Add `@require_kiro_token` to all Kiro overlay routes
- [ ] Add `/kiro/health` endpoint (returns 200 + Beast uptime)
- [ ] Add `/api/session/state` endpoint for conversation continuity

### Mac Client
- [ ] Build `kiro-mac-client` project with structure above
- [ ] Test pywebview + local proxy against Beast over Tailscale
- [ ] First-run token prompt → Keychain storage
- [ ] Connection health indicator in UI
- [ ] rumps menubar icon with persona switcher + status ping
- [ ] Dock toggle via AppKit (runtime show/hide from menubar menu)
- [ ] Prepare menubar_icon.png and menubar_icon_err.png (22×22 template images)
- [ ] Package with py2app → test `KIRO.app` launches cleanly
- [ ] Copy `.app` to iMac Applications folder

### Security Posture
- [ ] Auth token stored in macOS Keychain (not disk, not config file)
- [ ] All traffic over Tailscale (WireGuard encrypted) + TLS
- [ ] Token hashed with bcrypt in PostgreSQL (plaintext never stored server-side)
- [ ] Token revocation: set `revoked = TRUE` in DB, client gets 401, re-prompts
- [ ] No conversation data at rest on the Mac — everything lives on the Beast
- [ ] Local proxy binds to 127.0.0.1 only (not exposed to LAN)

---

## Summary

The architecture is:

```
┌──────────────────────────┐     Tailscale (WireGuard)     ┌──────────────────────┐
│   Work iMac              │◄──────────────────────────────►│   The Beast          │
│                          │         HTTPS + Bearer         │                      │
│  ┌────────────────────┐  │                                │  ┌────────────────┐  │
│  │  KIRO.app          │  │                                │  │ Flask :5050    │  │
│  │  ┌──────────────┐  │  │                                │  │ /kiro/overlay  │  │
│  │  │ pywebview    │  │  │                                │  │ /kiro/health   │  │
│  │  │ (main window)│  │  │                                │  │ /api/session/* │  │
│  │  └──────┬───────┘  │  │                                │  ├────────────────┤  │
│  │  ┌──────┴───────┐  │  │                                │  │ PostgreSQL     │  │
│  │  │ local proxy  │──┼──┼── Bearer token in header ─────►│  │ (all state)    │  │
│  │  │ :5051        │  │  │                                │  │ Token hashes   │  │
│  │  └──────────────┘  │  │                                │  └────────────────┘  │
│  │  ┌──────────────┐  │  │                                │                      │
│  │  │ rumps        │  │  │                                │                      │
│  │  │ menubar icon │  │  │                                │                      │
│  │  │ • status dot │  │  │                                │                      │
│  │  │ • persona ▸  │  │  │                                │                      │
│  │  │ • show/hide  │  │  │                                │                      │
│  │  │ • dock toggle│  │  │                                │                      │
│  │  └──────────────┘  │  │                                │                      │
│  └────────────────────┘  │                                │                      │
│                          │                                │                      │
│  Token in Keychain       │                                │                      │
└──────────────────────────┘                                └──────────────────────┘
```

Zero data at rest on the Mac. All brains on the Beast. Tailscale + TLS + bearer token = three layers of auth. Conversations pick up exactly where they left off. Menubar icon is always present for status + persona switching + show/hide. Dock icon toggleable on the fly via AppKit — run it as a full desktop citizen or a ghost overlay, your call.
