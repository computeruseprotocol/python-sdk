# Cross-OS Agent Demo

Control multiple machines running different operating systems from Claude Code (or any MCP client), all through the same CUP accessibility tree format.

This demonstrates CUP's core value: **one protocol, every OS**. Claude sees identical UI trees whether a machine runs Windows, macOS, or Linux — no platform-specific prompting needed.

## Architecture

```
┌──────────────────────────────────────────────┐
│           Claude Code / MCP Client           │
│  "Open Notepad on windows, type hello,       │
│   then open Notes on mac and paste it"       │
└──────────────────┬───────────────────────────┘
                   │ MCP (stdio)
┌──────────────────▼───────────────────────────┐
│          mcp_server.py (MCP bridge)          │
│  Exposes snapshot, action, find tools        │
│  for each connected machine                  │
└──────┬──────────────────────────┬────────────┘
       │ WebSocket                │ WebSocket
┌──────▼──────┐            ┌─────▼────────────┐
│ Windows PC  │            │ Mac              │
│ cup_server  │            │ cup_server       │
│ (UIA)       │            │ (AXUIElement)    │
└─────────────┘            └──────────────────┘
```

## Files

| File | Purpose |
|------|---------|
| `cup_server.py` | WebSocket server wrapping `cup.Session()` — run on each machine |
| `mcp_server.py` | MCP bridge — connects to remote cup_servers, exposes tools to Claude Code |
| `cup_remote.py` | Client library: `RemoteSession` and `MultiSession` |
| `agent.py` | Standalone agent (alternative to MCP — runs its own Claude loop) |

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

Replace the paths and IPs for your setup. If you're running Claude Code on your Windows machine, `windows` can point to `localhost`.

### 4. Talk to Claude

Now just ask Claude Code naturally:

```
"What apps are open on both machines?"

"Open Notepad on windows and type 'Hello from Mac', then open TextEdit on mac and type 'Hello from Windows'"

"Take a snapshot of the foreground window on mac"

"Click the Submit button on windows"
```

Claude sees the CUP tools (`snapshot_machine`, `act_on_machine`, etc.) and uses them to interact with both machines.

## Standalone Agent (alternative)

If you prefer a self-contained script instead of MCP:

```bash
pip install anthropic websocket-client

python agent.py \
    windows=ws://localhost:9800 \
    mac=ws://192.168.1.30:9800 \
    --task "Open a text editor on both machines and type today's date"

# Or interactive mode
python agent.py windows=ws://localhost:9800 mac=ws://192.168.1.30:9800
```

## Example tasks

```
# Cross-OS text relay
"Copy the title of the focused window on Windows and type it into the terminal on Mac"

# Parallel app launch
"Open a text editor on both machines and type today's date in each"

# Cross-OS comparison
"Take a snapshot of both machines and tell me what apps are running on each"

# Multi-step workflow
"On Windows, open Chrome and navigate to example.com. On Mac, open Safari and navigate to the same URL."
```

## Using the client library directly

```python
from cup_remote import RemoteSession, MultiSession

# Single machine
with RemoteSession("ws://192.168.1.10:9800") as win:
    print(win.snapshot(scope="overview"))
    win.open_app("notepad")
    tree = win.snapshot(scope="foreground")

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

Methods: `snapshot`, `action`, `press`, `find`, `overview`, `open_app`, `batch`, `info`
