# API Reference

## Session

The primary interface for CUP. Captures accessibility trees and executes actions.

```python
import cup

session = cup.Session(platform=None)
```

**Parameters:**
- `platform` (str | None) — Force a specific platform adapter (`"windows"`, `"macos"`, `"linux"`, `"web"`). Auto-detected if `None`.

---

### session.snapshot()

Capture the accessibility tree.

```python
result = session.snapshot(
    scope="foreground",   # "overview" | "foreground" | "desktop" | "full"
    app=None,             # filter by window title (scope="full" only)
    max_depth=999,        # maximum tree depth
    compact=True,         # True → compact text, False → CUP envelope dict
    detail="compact",     # "compact" | "full"
)
```

**Scopes:**

| Scope | What it captures | Tree walking |
|-------|-----------------|-------------|
| `overview` | Window list only | No (near-instant) |
| `foreground` | Active window tree + window list header | Yes |
| `desktop` | Desktop surface (icons, widgets) | Yes |
| `full` | All windows | Yes |

**Returns:** `str` (compact text) or `dict` (CUP envelope), depending on `compact`.

**Detail levels:**

| Level | Behavior |
|-------|----------|
| `compact` | Prunes unnamed generics, empty text, decorative images (~75% smaller) |
| `full` | No pruning — every node included |

---

### session.action()

Perform an action on an element from the last snapshot.

```python
result = session.action("e14", "click")
result = session.action("e5", "type", value="hello world")
result = session.action("e9", "scroll", direction="down")
```

**Parameters:**
- `element_id` (str) — Element ID from the tree (e.g., `"e14"`). Only valid for the most recent snapshot.
- `action` (str) — One of the canonical actions below.
- `**params` — Action-specific parameters.

**Canonical actions:**

| Action | Parameters | Description |
|--------|-----------|-------------|
| `click` | — | Click/invoke the element |
| `collapse` | — | Collapse an expanded element |
| `decrement` | — | Decrement a slider/spinbutton |
| `dismiss` | — | Dismiss a dialog/popup |
| `doubleclick` | — | Double-click |
| `expand` | — | Expand a collapsed element |
| `focus` | — | Move keyboard focus to the element |
| `increment` | — | Increment a slider/spinbutton |
| `longpress` | — | Long-press (touch/mobile interaction) |
| `rightclick` | — | Right-click (context menu) |
| `scroll` | `direction: str` | Scroll container (`up`/`down`/`left`/`right`) |
| `select` | — | Select an item in a list/tree/tab |
| `setvalue` | `value: str` | Set element value programmatically |
| `toggle` | — | Toggle checkbox or switch |
| `type` | `value: str` | Type text into a field |

**Returns:** `ActionResult`

```python
@dataclass
class ActionResult:
    success: bool
    message: str
    error: str | None = None
```

---

### session.press()

Send a keyboard shortcut.

```python
result = session.press("ctrl+s")
result = session.press("alt+f4")
result = session.press("enter")
```

**Parameters:**
- `combo` (str) — Key combination. Modifiers: `ctrl`, `alt`, `shift`, `win`/`cmd`. Joined with `+`.

---

### session.open_app()

Open an application by name with fuzzy matching.

```python
result = session.open_app("chrome")     # → Google Chrome
result = session.open_app("code")       # → Visual Studio Code
result = session.open_app("notepad")    # → Notepad
```

**Parameters:**
- `name` (str) — Application name (fuzzy matched against installed apps).

**Returns:** `ActionResult`. Waits for the app window to appear.

---

### session.find()

Search the last captured tree without re-capturing.

```python
results = session.find(query="play button")
results = session.find(role="textbox", state="focused")
results = session.find(name="Submit")
```

**Parameters:**
- `query` (str | None) — Freeform semantic query. Automatically parsed into role + name signals.
- `role` (str | None) — Role filter. Accepts CUP roles or synonyms (e.g., `"search bar"` matches `searchbox`/`textbox`).
- `name` (str | None) — Name filter with fuzzy token matching.
- `state` (str | None) — Exact state match (e.g., `"focused"`, `"disabled"`).
- `limit` (int) — Max results (default 5).

**Returns:** List of CUP node dicts (without children), ranked by relevance.

---

### session.batch()

Execute a sequence of actions, stopping on first failure.

```python
results = session.batch([
    {"element_id": "e3", "action": "click"},
    {"action": "wait", "ms": 500},
    {"element_id": "e7", "action": "type", "value": "hello"},
    {"action": "press", "keys": "enter"},
])
```

**Action spec format:**

| Key | Required | Description |
|-----|----------|-------------|
| `action` | Yes | Action name |
| `element_id` | For element actions | Target element |
| `value` | For `type`/`setvalue` | Text value |
| `direction` | For `scroll` | Scroll direction |
| `keys` | For `press` | Key combination |
| `ms` | For `wait` | Delay in ms (50-5000) |

**Returns:** List of `ActionResult` — stops at first failure.

---

### session.screenshot()

Capture a screenshot as PNG bytes.

```python
png_bytes = session.screenshot()
png_bytes = session.screenshot(region={"x": 100, "y": 200, "w": 800, "h": 600})
```

Requires: `pip install computeruseprotocol[screenshot]`

**Parameters:**
- `region` (dict | None) — Capture region `{"x", "y", "w", "h"}` in pixels. `None` for full primary monitor.

**Returns:** `bytes` (PNG image data).

---

## Convenience Functions

Thin wrappers around a default `Session` instance. Useful for quick scripting.

```python
import cup

# Foreground window as compact text (the default)
text = cup.snapshot()

# All windows as compact text
text = cup.snapshot("full")

# Foreground window as CUP envelope dict
envelope = cup.snapshot_raw()

# All windows as CUP envelope dict
envelope = cup.snapshot_raw("full")

# Window list only (no tree walking)
text = cup.overview()
```

---

## CUP Envelope Format

The JSON envelope returned by `session.snapshot(compact=False)`:

```json
{
    "version": "0.1.0",
    "platform": "windows",
    "timestamp": 1740067200000,
    "screen": { "w": 2560, "h": 1440, "scale": 1.0 },
    "scope": "foreground",
    "app": { "name": "Discord", "pid": 1234 },
    "tree": [ ... ]
}
```

### Node format

Each node in the tree:

```json
{
    "id": "e14",
    "role": "button",
    "name": "Submit",
    "bounds": { "x": 120, "y": 340, "w": 88, "h": 36 },
    "states": ["focused"],
    "actions": ["click"],
    "value": null,
    "children": [],
    "platform": { ... }
}
```

**Roles:** 54 ARIA-derived roles. See [schema/mappings.json](../schema/mappings.json) for the full list and per-platform mappings.

**States:** `busy`, `checked`, `collapsed`, `disabled`, `editable`, `expanded`, `focused`, `hidden`, `mixed`, `modal`, `multiselectable`, `offscreen`, `pressed`, `readonly`, `required`, `selected`

**Element actions:** `click`, `collapse`, `decrement`, `dismiss`, `doubleclick`, `expand`, `focus`, `increment`, `longpress`, `rightclick`, `scroll`, `select`, `setvalue`, `toggle`, `type`

**Session-level actions:** `press`

---

## Compact Format

The text format returned by `session.snapshot(compact=True)`. Optimized for LLM context windows (~75% smaller than JSON).

```
# CUP 0.1.0 | windows | 2560x1440
# app: Discord
# 87 nodes (353 before pruning)

[e0] win "Discord" 509,62 1992x1274
  [e1] doc "General" 509,62 1992x1274 {ro}
    [e2] btn "Back" 518,66 26x24 [clk]
    [e7] tre "Servers" 509,94 72x1242
      [e8] ti "Lechownia" 513,190 64x48 {sel} [clk,sel]
```

Line format: `[id] role "name" x,y wxh {states} [actions] val="value" (attrs)`

Full spec: [compact.md](https://github.com/computeruseprotocol/computeruseprotocol/blob/main/schema/compact.md)

---

## MCP Server

CUP ships an MCP server for integration with AI agents (Claude, Copilot, etc.).

```bash
# Run directly
cup-mcp

# Or via Python
python -m cup.mcp
```

### MCP Tools

| Tool | Description |
|------|-------------|
| `snapshot()` | Capture active window tree (compact) |
| `snapshot_app(app)` | Capture specific app by title |
| `overview()` | Window list only (near-instant) |
| `snapshot_desktop()` | Desktop surface (icons, widgets) |
| `find(query, role, name, state)` | Search last tree |
| `action(action, element_id, ...)` | Perform action on element |
| `open_app(name)` | Open app by name |
| `screenshot(region)` | Capture screenshot |

### Configuration

Add to your MCP client config (e.g., `.mcp.json` for Claude Code):

```json
{
    "mcpServers": {
        "cup": {
            "command": "cup-mcp",
            "args": []
        }
    }
}
```

---

## PlatformAdapter

Abstract base class for adding new platform support.

```python
from cup._base import PlatformAdapter

class AndroidAdapter(PlatformAdapter):
    @property
    def platform_name(self) -> str:
        return "android"

    def initialize(self) -> None: ...
    def get_screen_info(self) -> tuple[int, int, float]: ...
    def get_foreground_window(self) -> dict: ...
    def get_all_windows(self) -> list[dict]: ...
    def get_window_list(self) -> list[dict]: ...
    def get_desktop_window(self) -> dict | None: ...
    def capture_tree(self, windows, *, max_depth=999) -> tuple[list, dict, dict]: ...
```

See [cup/_base.py](../cup/_base.py) for the full interface with docstrings.
