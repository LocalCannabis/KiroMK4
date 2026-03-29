#!/usr/bin/env python3
"""
ui/overlay.py — KIRO Transparent Floating Overlay

A GTK3 + WebKitGTK transparent, borderless, always-on-top overlay window
that renders the KIRO Jarvis-style HUD. Starts as a collapsed blue pill
and expands on click to the full command interface.

Requires system Python (not conda) with:
  - gir1.2-gtk-3.0
  - gir1.2-webkit2-4.1 (or 4.0)
  - A compositor (GNOME, KDE, Picom, etc.)

Usage:
    /usr/bin/python3 ui/overlay.py                # default
    /usr/bin/python3 ui/overlay.py --port 5199    # custom Flask port
    /usr/bin/python3 ui/overlay.py --position tl  # top-left corner
"""

from __future__ import annotations

import argparse
import json
import signal
import subprocess
import sys
import urllib.request

# ── GTK / WebKit imports ────────────────────────────────────────────────────

try:
    import gi

    gi.require_version("Gtk", "3.0")
    gi.require_version("Gdk", "3.0")

    # Try WebKit2 4.1 first (newer Ubuntu), fall back to 4.0
    try:
        gi.require_version("WebKit2", "4.1")
    except ValueError:
        gi.require_version("WebKit2", "4.0")

    from gi.repository import Gdk, GLib, Gtk, WebKit2
except Exception as exc:
    print(f"[KIRO overlay] Missing GTK/WebKit dependencies: {exc}")
    print("Install with:  sudo apt install gir1.2-gtk-3.0 gir1.2-webkit2-4.1")
    sys.exit(1)

# ── Constants ───────────────────────────────────────────────────────────────

# CSS logical dimensions (zoom-independent — WebKit zoom scales these to physical px)
PILL_W_CSS,  PILL_H_CSS  = 140, 42
PANEL_W_CSS, PANEL_H_CSS = 520, 780
EDGE_MARGIN = 20  # px from screen edge


class KiroOverlay(Gtk.Window):
    """
    Transparent, borderless, always-on-top GTK window embedding a WebKit
    view that loads the KIRO HUD interface from the Flask server.
    """

    def __init__(self, flask_url: str, position: str = "tr", zoom: float = 2.0) -> None:
        super().__init__(type=Gtk.WindowType.TOPLEVEL)

        self._flask_url = flask_url
        self._position = position
        self._expanded = False
        self._pending_reveal = False
        # Physical pixel dimensions = CSS logical × zoom
        self._zoom    = zoom
        self._pill_w  = int(PILL_W_CSS  * zoom)
        self._pill_h  = int(PILL_H_CSS  * zoom)
        self._panel_w = int(PANEL_W_CSS * zoom)
        self._panel_h = int(PANEL_H_CSS * zoom)

        # ── Window properties ───────────────────────────────────────────
        self.set_title("KIRO")
        self.set_decorated(False)
        self.set_keep_above(True)
        self.set_skip_taskbar_hint(True)
        self.set_skip_pager_hint(True)
        self.stick()  # visible on all workspaces
        self.set_type_hint(Gdk.WindowTypeHint.UTILITY)
        self.set_default_size(self._pill_w, self._pill_h)

        # ── RGBA transparency ──────────────────────────────────────────
        screen = self.get_screen()
        visual = screen.get_rgba_visual()
        if visual:
            self.set_visual(visual)
        self.set_app_paintable(True)
        self.connect("draw", self._on_draw)

        # ── WebKit view ────────────────────────────────────────────────
        ctx = WebKit2.WebContext.get_default()
        content_mgr = WebKit2.UserContentManager()

        # Register JS → Python message handler
        content_mgr.register_script_message_handler("kiro")
        content_mgr.connect("script-message-received::kiro", self._on_js_message)

        self._webview = WebKit2.WebView.new_with_user_content_manager(content_mgr)
        self._webview.set_background_color(Gdk.RGBA(0, 0, 0, 0))

        # Enable dev tools in debug builds
        settings = self._webview.get_settings()
        settings.set_enable_developer_extras(True)
        settings.set_enable_javascript(True)
        settings.set_allow_file_access_from_file_urls(True)
        # Disable GPU compositing — on transparent windows the GPU layer cache
        # retains stale frames causing ghost text artifacts. Software rendering
        # is pixel-exact and clears properly on every repaint.
        settings.set_hardware_acceleration_policy(
            WebKit2.HardwareAccelerationPolicy.NEVER
        )
        # Single call scales all CSS fonts/spacing/elements to physical pixels
        self._webview.set_zoom_level(zoom)

        self.add(self._webview)

        # ── Signals ────────────────────────────────────────────────────
        self.connect("destroy", Gtk.main_quit)
        self.connect("key-press-event", self._on_key_press)
        self.connect("configure-event", self._on_configure)

        # ── Position and load ──────────────────────────────────────────
        self.show_all()
        self._position_window(self._pill_w, self._pill_h)
        self._webview.load_uri(f"{flask_url}/hud")

    # ── Drawing ─────────────────────────────────────────────────────────

    def _on_draw(self, widget, cr):
        """Paint fully transparent background — WebKit renders on top."""
        cr.set_source_rgba(0, 0, 0, 0)
        cr.set_operator(0)  # CAIRO_OPERATOR_CLEAR
        cr.paint()
        cr.set_operator(2)  # CAIRO_OPERATOR_OVER
        return False

    # ── Positioning ─────────────────────────────────────────────────────

    def _position_window(self, w: int, h: int):
        """Position window according to self._position (tl, tr, bl, br)."""
        display = Gdk.Display.get_default()
        monitor = display.get_primary_monitor() or display.get_monitor(0)
        geom = monitor.get_geometry()

        positions = {
            "tr": (geom.x + geom.width - w - EDGE_MARGIN, geom.y + EDGE_MARGIN),
            "tl": (geom.x + EDGE_MARGIN, geom.y + EDGE_MARGIN),
            "br": (geom.x + geom.width - w - EDGE_MARGIN, geom.y + geom.height - h - EDGE_MARGIN),
            "bl": (geom.x + EDGE_MARGIN, geom.y + geom.height - h - EDGE_MARGIN),
        }
        x, y = positions.get(self._position, positions["tr"])
        self.move(x, y)

    # ── JS → Python messages ────────────────────────────────────────────

    def _on_js_message(self, content_mgr, result):
        """Handle messages from the HUD JavaScript."""
        try:
            js_value = result.get_js_value()
            msg_str = js_value.to_string()
            msg = json.loads(msg_str)
        except Exception:
            return

        action = msg.get("action")

        if action == "expand":
            self._expanded = True
            self._pending_reveal = True
            self.set_size_request(self._panel_w, self._panel_h)
            self.resize(self._panel_w, self._panel_h)
            self._position_window(self._panel_w, self._panel_h)
            GLib.timeout_add(400, self._reveal_fallback)

        elif action == "collapse":
            self._expanded = False
            self._pending_reveal = False
            self.set_size_request(self._pill_w, self._pill_h)
            self.resize(self._pill_w, self._pill_h)
            self._position_window(self._pill_w, self._pill_h)

        elif action == "set_zoom":
            zoom = float(msg.get("value", self._zoom))
            zoom = max(1.0, min(3.0, zoom))
            self._zoom    = zoom
            self._pill_w  = int(PILL_W_CSS  * zoom)
            self._pill_h  = int(PILL_H_CSS  * zoom)
            self._panel_w = int(PANEL_W_CSS * zoom)
            self._panel_h = int(PANEL_H_CSS * zoom)
            self._webview.set_zoom_level(zoom)
            if self._expanded:
                # Just resize + reposition — panel is already visible, no reveal cycle needed
                self.set_size_request(self._panel_w, self._panel_h)
                self.resize(self._panel_w, self._panel_h)
                self._position_window(self._panel_w, self._panel_h)
            else:
                self.set_size_request(self._pill_w, self._pill_h)
                self.resize(self._pill_w, self._pill_h)
                self._position_window(self._pill_w, self._pill_h)

        elif action == "set_position":
            pos = msg.get("value", "tr")
            if pos in ("tr", "tl", "br", "bl"):
                self._position = pos
                w = self._panel_w if self._expanded else self._pill_w
                h = self._panel_h if self._expanded else self._pill_h
                self._position_window(w, h)

        elif action == "set_opacity":
            # Compositor-level opacity — instant, no WebKit repaint needed
            alpha = float(msg.get("value", 1.0))
            alpha = max(0.1, min(1.0, alpha))
            gdk_win = self.get_window()
            if gdk_win:
                gdk_win.set_opacity(alpha)
            else:
                # Fallback before window is realized
                self.set_opacity(alpha)

        elif action == "drag_start":
            # JS sends the mousedown clientX/Y; we add window origin for screen coords.
            client_x = int(msg.get("x", 0))
            client_y = int(msg.get("y", 0))
            wx, wy = self.get_position()
            gdk_win = self.get_window()
            if gdk_win:
                self.begin_move_drag(1, wx + client_x, wy + client_y, Gdk.CURRENT_TIME)

        elif action == "quit":
            Gtk.main_quit()

    # ── Helpers ────────────────────────────────────────────────────────

    def _on_configure(self, widget, event):
        """Fires when WM has actually applied a size change.
        Triggers revealPanel() as soon as the window reaches panel dimensions."""
        if self._pending_reveal and event.width >= self._panel_w - 10:
            self._pending_reveal = False
            GLib.idle_add(self._reveal_panel)
        return False

    def _reveal_fallback(self):
        """Safety net: if configure-event didn't trigger reveal in 400ms, do it anyway."""
        if self._pending_reveal:
            self._pending_reveal = False
            self._reveal_panel()
        return False  # GLib.SOURCE_REMOVE

    def _reveal_panel(self):
        """Tell WebKit JS to show the panel — called only after GTK resize is confirmed."""
        self._run_js("revealPanel()")
        return False  # GLib.SOURCE_REMOVE

    # ── Keyboard ────────────────────────────────────────────────────────

    def _on_key_press(self, widget, event):
        """Global key handlers."""
        key = Gdk.keyval_name(event.keyval)

        # Escape collapses; if already collapsed, quits
        if key == "Escape":
            if self._expanded:
                self._run_js("collapsePanel()")
            else:
                Gtk.main_quit()
            return True

        return False

    # ── Helpers ─────────────────────────────────────────────────────────

    def _run_js(self, code: str):
        """Execute JavaScript in the webview."""
        self._webview.run_javascript(code, None, None, None)


# ── Server check ────────────────────────────────────────────────────────────

def _wait_for_server(url: str, timeout: int = 10) -> bool:
    """Wait for the Flask server to be reachable."""
    import time

    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            urllib.request.urlopen(url, timeout=2)
            return True
        except Exception:
            time.sleep(0.5)
    return False


# ── Main ────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="KIRO Floating Overlay")
    parser.add_argument("--port", type=int, default=5199, help="Flask server port")
    parser.add_argument(
        "--position",
        choices=["tr", "tl", "br", "bl"],
        default="tr",
        help="Corner position: tr=top-right, tl=top-left, br=bottom-right, bl=bottom-left",
    )
    parser.add_argument(
        "--zoom",
        type=float,
        default=2.0,
        help="UI zoom level — scales all fonts and element sizes (default: 2.0)",
    )
    args = parser.parse_args()

    flask_url = f"http://127.0.0.1:{args.port}"

    # Check Flask server
    print(f"[KIRO] Connecting to {flask_url} ...")
    if not _wait_for_server(flask_url):
        print(f"[KIRO] Flask server not reachable at {flask_url}")
        print("[KIRO] Start it first:  python ui/launcher.py --headless")
        sys.exit(1)
    print(f"[KIRO] Server ready. Launching overlay.")

    # Graceful shutdown on signals
    signal.signal(signal.SIGINT, lambda *_: Gtk.main_quit())
    signal.signal(signal.SIGTERM, lambda *_: Gtk.main_quit())
    GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGINT, Gtk.main_quit)

    overlay = KiroOverlay(flask_url, position=args.position, zoom=args.zoom)
    Gtk.main()


if __name__ == "__main__":
    main()
