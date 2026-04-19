# KIRO Vault — Obsidian Architecture Workspace

This vault lives inside the Kiro repo at `vault/` and is the single source of
truth for visualizing the KIRO multi-persona AI system.

---

## Quick Start

### 1. Install Obsidian (Linux)

```bash
# AppImage (recommended — no root needed, auto-updates)
wget -qO ~/Applications/Obsidian.AppImage \
  https://github.com/obsidianmd/obsidian-releases/releases/latest/download/Obsidian.AppImage
chmod +x ~/Applications/Obsidian.AppImage

# Or .deb
# wget -qO /tmp/obsidian.deb \
#   https://github.com/obsidianmd/obsidian-releases/releases/latest/download/obsidian_amd64.deb
# sudo dpkg -i /tmp/obsidian.deb
```

### 2. Open this vault

```
File → Open Vault → Open folder as vault → select this "vault" directory
```

### 3. Install plugins

Go to **Settings → Community plugins → Turn on community plugins**, then
install:

| Plugin | ID | Purpose |
|---|---|---|
| **Advanced Canvas** | `obsidian-advanced-canvas` | Flowchart shapes, edge labels, canvas events |
| **Local REST API** | `obsidian-local-rest-api` | HTTPS endpoint on `localhost:27124` for programmatic reads/writes |
| **Dataview** | `dataview` | Query frontmatter across persona/KB/spec files |

### 4. Configure Local REST API

1. Enable the plugin in Settings → Community plugins.
2. Open its settings pane — it will auto-generate an API key on first run.
3. Copy the key (you'll see it labeled **API Key**).
4. The plugin serves HTTPS on `https://localhost:27124` with a self-signed
   cert.

**Test it:**

```bash
# Replace YOUR_API_KEY with the generated key.
# -k skips TLS verification for the self-signed cert.
curl -k -H "Authorization: Bearer 6482a3e875f897b74d87b9234ddc20238f4bad7a4c651a42f1dd36561d64afbf" \
  https://localhost:27124/vault/README.md
```

---

## How Copilot ↔ Obsidian Interaction Works

### Reading

| Method | When |
|---|---|
| **Direct file read** (`vault/Kiro System.canvas`) | Passive analysis — Copilot reads the JSON on disk in the workspace mount. No API needed. |
| **Local REST API GET** | Live queries when the canvas is open and may have unsaved changes in Obsidian's buffer. |

### Writing

| Method | When |
|---|---|
| **Direct file write** | Batch changes (new persona, new canvas). Obsidian hot-reloads on next focus. |
| **Local REST API PUT / PATCH** | Live edits that update the UI immediately without re-focusing. |

### Key API Endpoints

```
GET    /vault/{filepath}          → raw file content
PUT    /vault/{filepath}          → overwrite file
PATCH  /vault/{filepath}          → partial content update (append, insert)
GET    /vault/                    → list vault files
POST   /open/{filepath}           → open a file in the Obsidian UI
```

All requests require `Authorization: Bearer <API_KEY>` header.  
Base URL: `https://localhost:27124`  
Use `-k` with curl (self-signed cert) or configure the cert in your HTTP
client.

### Expected Workflow Feel

1. **You** drag nodes, draw edges, rearrange groups in the Obsidian canvas UI.
2. **Copilot** reads the updated `.canvas` JSON on your next turn and responds
   to the new state — no manual export needed.
3. **Copilot** proposes structural changes by writing directly to the canvas
   JSON. You see them appear in the UI (hot-reload or via the REST API).
4. **You** accept, tweak, or revert. Rinse and repeat.

---

## `.canvas` JSON Schema Reference

```jsonc
{
  "nodes": [
    {
      "id":     "abc123",       // unique string
      "type":   "text",         // "text" | "file" | "link" | "group"
      "x":      100,            // canvas X coordinate
      "y":      200,            // canvas Y coordinate
      "width":  250,            // node width in px
      "height": 120,            // node height in px
      "color":  "1",            // Obsidian palette: "1"-"6" (adapts to theme)
      "text":   "# Node title", // markdown content (type=text)
      "file":   "path/to.md",   // vault-relative path (type=file)
      "label":  "Group name"    // display label (type=group)
    }
  ],
  "edges": [
    {
      "id":       "edge001",
      "fromNode": "abc123",
      "toNode":   "def456",
      "fromSide": "right",      // "top" | "right" | "bottom" | "left"
      "toSide":   "left",
      "label":    "uses voice", // edge label text
      "color":    "3"           // optional palette color
    }
  ]
}
```

### Color Palette Key

| Color | Meaning |
|-------|---------|
| `"1"` (red) | Personas |
| `"2"` (orange) | Knowledge Bases |
| `"3"` (yellow) | Voice Assignments |
| `"4"` (green) | Memory Tiers |
| `"5"` (cyan) | Spec Documents |
| `"6"` (purple) | Groups / Placeholder |

---

## Commit Strategy

- **Structural canvas changes** (new persona, new integration wired up):
  commit normally on your working branch.
- **Thinking-session scratch edits**: the `vault/scratch/` folder is
  gitignored — use it for throwaway canvases and brainstorm files.
- The `.obsidian/workspace.json` file (Obsidian UI state) is gitignored so
  your pane layout doesn't pollute history.
- Canvas files outside `scratch/` are version-controlled.

---

## Folder Structure

```
vault/
├── README.md                  ← you are here
├── Kiro System.canvas         ← main architecture graph
├── personas/                  ← one .md per persona
├── knowledge/                 ← one .md per knowledge source
├── memory-tiers/              ← one .md per memory tier
├── voices/                    ← one .md per voice engine
├── specs/                     ← one .md per design doc / spec
├── scratch/                   ← gitignored thinking-session canvases
└── .obsidian/                 ← plugin config (mostly gitignored)
```
