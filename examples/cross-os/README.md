# Cross-OS Agent Demo

An AI agent that performs tasks across multiple machines running different operating systems, all using the same CUP (Computer Use Protocol) format.

This demonstrates CUP's core value: **one protocol, every OS**. The agent sees identical accessibility tree formats whether a machine runs Windows, macOS, or Linux — no platform-specific code needed.

## Architecture

```
┌──────────────────────────────────────────────┐
│              Agent (agent.py)                 │
│  Claude interprets CUP trees from all        │
│  machines and coordinates actions across them │
└──────┬──────────────┬──────────────┬─────────┘
       │              │              │
   WebSocket      WebSocket      WebSocket
       │              │              │
┌──────▼──────┐ ┌─────▼───────┐ ┌───▼─────────┐
│ Windows PC  │ │ Linux Box   │ │ Mac         │
│ cup_server  │ │ cup_server  │ │ cup_server  │
│ (UIA)       │ │ (AT-SPI2)   │ │ (AXUIElement│
└─────────────┘ └─────────────┘ └─────────────┘
```

## Files

| File | Purpose |
|------|---------|
| `cup_server.py` | WebSocket server that wraps `cup.Session()` — run on each machine |
| `cup_remote.py` | Client library: `RemoteSession` (single machine) and `MultiSession` (multi-machine) |
| `agent.py` | The orchestrator: connects to all machines, uses Claude to perform cross-OS tasks |

## Setup

### 1. Install dependencies on every machine

```bash
pip install computeruseprotocol websockets
```

### 2. Start the server on each machine

```bash
# On your Windows PC (e.g., 192.168.1.10)
python cup_server.py --port 9800

# On your Linux box (e.g., 192.168.1.20)
python cup_server.py --port 9800

# On your Mac (e.g., 192.168.1.30)
python cup_server.py --port 9800
```

### 3. Run the agent from any machine

```bash
# Install agent dependencies
pip install anthropic websocket-client

# Set your API key
export ANTHROPIC_API_KEY=sk-ant-...

# Run with a task
python agent.py \
    windows=ws://192.168.1.10:9800 \
    linux=ws://192.168.1.20:9800 \
    --task "Open Notepad on Windows and type 'Hello from Linux', then open gedit on Linux and type 'Hello from Windows'"

# Or interactive mode
python agent.py windows=ws://192.168.1.10:9800 linux=ws://192.168.1.20:9800
```

## Example tasks

```
# Cross-OS text relay
"Copy the title of the focused window on Windows and type it into the terminal on Linux"

# Parallel app launch
"Open a text editor on both machines and type today's date in each"

# Cross-OS comparison
"Take a snapshot of both machines and tell me what apps are running on each"

# Multi-step workflow
"On Windows, open Chrome and navigate to example.com. On Linux, open Firefox and navigate to the same URL."
```

## Using the client library directly

You don't need the agent to use cross-OS CUP. The client library works standalone:

```python
from cup_remote import RemoteSession, MultiSession

# Single machine
with RemoteSession("ws://192.168.1.10:9800") as win:
    print(win.snapshot(scope="overview"))
    win.open_app("notepad")
    tree = win.snapshot(scope="foreground")
    # find the text area, type into it, etc.

# Multiple machines
with MultiSession({
    "win": "ws://192.168.1.10:9800",
    "linux": "ws://192.168.1.20:9800",
}) as multi:
    # Snapshot all machines in parallel
    trees = multi.snapshot_all(scope="foreground")
    for name, tree in trees.items():
        print(f"--- {name} ---")
        print(tree[:200])  # first 200 chars

    # Act on specific machines
    multi["win"].action("e5", "click")
    multi["linux"].action("e12", "type", value="hello")
```

## Protocol

The server uses a simple JSON-RPC protocol over WebSocket:

```json
// Request
{"id": 1, "method": "snapshot", "params": {"scope": "foreground"}}

// Response
{"id": 1, "result": "# CUP 0.1.0 | windows | 1920x1080\n..."}
```

Methods: `snapshot`, `action`, `press`, `find`, `overview`, `open_app`, `batch`, `info`
