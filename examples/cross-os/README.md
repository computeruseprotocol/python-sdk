# Cross-OS Agent Demo

Control multiple machines running different operating systems from Claude Code (or any MCP client), all through the same CUP accessibility tree format.

This demonstrates CUP's core value: **one protocol, every OS**. Claude sees identical UI trees whether a machine runs Windows, macOS, or Linux — no platform-specific prompting needed.

## Architecture

```
┌──────────────────────────────────────────────┐
│           AI agent / MCP Client           │
│  "Open Notepad on screen 1, type hello,      │
│   then open Notes on screen 2 and paste it"  │
└──────────────────┬───────────────────────────┘
                   │ MCP (stdio)
┌──────────────────▼───────────────────────────┐
│          mcp_server.py (MCP bridge)          │
│  Screen 1 = windows, Screen 2 = mac         │
│  Tools: snapshot, action, find, screenshot   │
└──────┬──────────────────────────┬────────────┘
       │ WebSocket                │ WebSocket
┌──────▼──────┐            ┌─────▼────────────┐
│ Windows PC  │            │ Mac              │
│ cup_server  │            │ cup_server       │
│ (UIA)       │            │ (AXUIElement)    │
└─────────────┘            └──────────────────┘
```

Each connected machine is a numbered **screen** (1, 2, 3...). Every tool accepts a `screen` parameter — either the number or the friendly name.

## Files

| File | Purpose |
|------|---------|
| `cup_server.py` | WebSocket server wrapping `cup.Session()` — run on each machine |
| `mcp_server.py` | MCP bridge — connects to remote cup_servers, exposes tools to Claude Code |
| `cup_remote.py` | Client library: `RemoteSession` and `MultiSession` |

## Quick Start (MCP + Claude Code)

### 1. Install dependencies on every machine

```bash
pip install computeruseprotocol websockets
```

### 2. Start cup_server on each machine

**On your Windows PC:**
```bash
python cup_server.py --host 0.0.0.0 --port 9800
```

**On your Mac:**
```bash
python cup_server.py --host 0.0.0.0 --port 9800
```

### 3. Add the MCP server to Claude Code

Install the MCP server dependencies (on the machine running Claude Code):
```bash
pip install mcp websocket-client
```

Add to your Claude Code MCP config:

```json
{
  "mcpServers": {
    "cup-cross-os": {
      "command": "python",
      "args": [
        "C:/path/to/examples/cross-os/mcp_server.py",
        "windows=ws://localhost:9800",
        "mac=ws://192.168.1.30:9800"
      ]
    }
  }
}
```

Replace the paths and IPs for your setup. Machines are numbered as screens (1, 2, 3...) in the order listed.

### 4. Talk to Claude

Now just ask Claude Code naturally:

```
"What apps are open on all screens?"

"Take a snapshot of screen 1"

"Open Notepad on windows and type 'Hello from Mac',
 then open TextEdit on mac and type 'Hello from Windows'"

"Click the Submit button on screen 2"

"Take a screenshot of screen 1"
```

## Available Tools

| Tool | Description |
|------|-------------|
| `list_screens()` | List all connected screens with number, name, OS |
| `snapshot(screen)` | Capture foreground window's UI tree |
| `snapshot_app(screen, app)` | Capture a specific app by title |
| `snapshot_desktop(screen)` | Capture desktop icons/widgets |
| `overview(screen)` | List open windows (near-instant) |
| `action(screen, action, ...)` | Click, type, press keys, scroll, etc. |
| `find(screen, query/role/name/state)` | Search the last tree for elements |
| `open_app(screen, app_name)` | Open an app by name (fuzzy match) |
| `screenshot(screen, region_*)` | Capture a PNG screenshot |
| `snapshot_all(scope)` | Snapshot all screens in parallel |

## Example tasks

```
# Cross-OS text relay
"Copy the title of the focused window on screen 1 and type it into the terminal on screen 2"

# Parallel app launch
"Open a text editor on all screens and type today's date in each"

# Cross-OS comparison
"Snapshot all screens and tell me what apps are running on each"

# Multi-step workflow
"On windows, open Chrome and navigate to example.com. On mac, open Safari and navigate to the same URL."
```

## Using the client library directly

```python
from cup_remote import RemoteSession, MultiSession

# Single machine
with RemoteSession("ws://192.168.1.10:9800") as win:
    print(win.snapshot(scope="overview"))
    win.open_app("notepad")
    tree = win.snapshot(scope="foreground")
    png = win.screenshot()  # full screen PNG bytes

# Multiple machines in parallel
with MultiSession({
    "win": "ws://192.168.1.10:9800",
    "mac": "ws://192.168.1.30:9800",
}) as multi:
    trees = multi.snapshot_all(scope="foreground")
    for name, tree in trees.items():
        print(f"--- {name} ---")
        print(tree[:200])

    multi["win"].action("e5", "click")
    multi["mac"].action("e12", "type", value="hello")
```

## Protocol

The cup_server uses a simple JSON-RPC protocol over WebSocket:

```json
{"id": 1, "method": "snapshot", "params": {"scope": "foreground"}}
{"id": 1, "result": "# CUP 0.1.0 | windows | 1920x1080\n..."}
```

Methods: `snapshot`, `snapshot_desktop`, `action`, `press`, `find`, `overview`, `open_app`, `screenshot`, `batch`, `info`
