"""CUP Cross-OS MCP Server — control multiple machines from Claude Code.

Connect this MCP server to Claude Code (or any MCP client) and talk to
multiple machines running different OSes through natural language.

Setup:
    1. Run cup_server.py on each machine you want to control
    2. Add this MCP server to your Claude Code config (see below)
    3. Ask Claude to do things across your machines

Claude Code config (~/.claude/claude_code_config.json):

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

Or run standalone for testing:

    python mcp_server.py windows=ws://localhost:9800 mac=ws://192.168.1.30:9800
"""

from __future__ import annotations

import json
import sys

from mcp.server.fastmcp import FastMCP

from cup_remote import MultiSession, RemoteSession

# ---------------------------------------------------------------------------
# Parse machine specs from argv
# ---------------------------------------------------------------------------

_machine_specs: dict[str, str] = {}
for arg in sys.argv[1:]:
    if "=" in arg:
        name, url = arg.split("=", 1)
        _machine_specs[name.strip()] = url.strip()

if not _machine_specs:
    print(
        "Usage: python mcp_server.py NAME=ws://host:port [NAME=ws://host:port ...]",
        file=sys.stderr,
    )
    print("Example: python mcp_server.py windows=ws://localhost:9800 mac=ws://192.168.1.30:9800", file=sys.stderr)
    sys.exit(1)

# ---------------------------------------------------------------------------
# Connect to all machines
# ---------------------------------------------------------------------------

_multi = MultiSession(_machine_specs)
try:
    _infos = _multi.connect_all()
except Exception as e:
    print(f"Failed to connect: {e}", file=sys.stderr)
    sys.exit(1)

_machine_list = ", ".join(
    f"{name} ({info['os']}, {info['machine']})" for name, info in _infos.items()
)

# ---------------------------------------------------------------------------
# MCP Server
# ---------------------------------------------------------------------------

mcp = FastMCP(
    name="cup-cross-os",
    instructions=(
        "CUP Cross-OS — control multiple machines through their UI.\n\n"
        f"Connected machines: {_machine_list}\n\n"
        "WORKFLOW:\n"
        "1. snapshot_machine to see a machine's UI\n"
        "2. find_on_machine to locate specific elements (avoids re-capturing)\n"
        "3. act_on_machine to interact (click, type, press, etc.)\n"
        "4. Re-snapshot ONLY after actions change the UI\n\n"
        "CUP FORMAT — each line is a UI element:\n"
        "  [id] role \"name\" @x,y wxh {states} [actions] val=\"value\"\n"
        "Element IDs (e.g., 'e14') are ephemeral — only valid for the most "
        "recent snapshot of THAT machine. After any action, re-snapshot.\n\n"
        "The tree format is IDENTICAL across all OSes — that's the point of CUP.\n"
        "Use list_machines to see what's available."
    ),
)

# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@mcp.tool()
def list_machines() -> str:
    """List all connected machines with their OS and hostname.

    Call this first to see what machines you can control.
    """
    lines = []
    for name, session in _multi.sessions.items():
        lines.append(f"- **{name}**: {session.machine} (os={session.os}, platform={session.platform_name})")
    return "\n".join(lines)


@mcp.tool()
def snapshot_machine(
    machine: str,
    scope: str = "foreground",
    app: str | None = None,
) -> str:
    """Capture the UI accessibility tree from a machine.

    Returns a CUP compact text representation where each element has an ID
    (e.g., 'e14') that can be used with act_on_machine.

    The format is identical across Windows, macOS, and Linux.

    Args:
        machine: Machine name (from list_machines).
        scope: What to capture:
            "foreground" — active window (default, most common)
            "full" — all visible windows
            "overview" — just the window list (near-instant)
        app: Filter to a specific app by title (only with scope="full").
    """
    if machine not in _multi:
        return f"Unknown machine '{machine}'. Use list_machines to see available machines."

    result = _multi[machine].snapshot(scope=scope, app=app, compact=True)
    return result if isinstance(result, str) else json.dumps(result)


@mcp.tool()
def act_on_machine(
    machine: str,
    action: str,
    element_id: str | None = None,
    value: str | None = None,
    direction: str | None = None,
    keys: str | None = None,
) -> str:
    """Perform a UI action on a specific machine.

    Element IDs come from the most recent snapshot_machine call for THAT machine.
    After performing an action, re-snapshot to get fresh IDs.

    Element actions (require element_id):
        click, doubleclick, rightclick, toggle, select,
        expand, collapse, scroll, type, setvalue

    Keyboard shortcut (no element_id needed):
        press — send keys like "ctrl+s", "enter", "alt+f4"

    Args:
        machine: Machine name.
        action: The action to perform.
        element_id: Element ID from the tree (e.g., "e14"). Required for all except "press".
        value: Text for "type" or "setvalue" actions.
        direction: Direction for "scroll" (up/down/left/right).
        keys: Key combo for "press" action (e.g., "ctrl+s").
    """
    if machine not in _multi:
        return json.dumps({"success": False, "error": f"Unknown machine '{machine}'"})

    session = _multi[machine]

    if action == "press":
        if not keys:
            return json.dumps({"success": False, "error": "press requires 'keys' parameter"})
        result = session.press(keys)
    else:
        if not element_id:
            return json.dumps({"success": False, "error": f"'{action}' requires 'element_id'"})
        params = {}
        if value is not None:
            params["value"] = value
        if direction is not None:
            params["direction"] = direction
        result = session.action(element_id, action, **params)

    return json.dumps({"success": result.success, "message": result.message, "error": result.error})


@mcp.tool()
def find_on_machine(
    machine: str,
    query: str,
) -> str:
    """Search the last captured tree on a machine for elements.

    Searches the FULL tree (including pruned elements) with semantic matching.
    Avoids the cost of re-capturing the entire tree.

    Examples: "submit button", "search input", "volume slider", "close"

    Args:
        machine: Machine name.
        query: Freeform query describing what to find.
    """
    if machine not in _multi:
        return f"Unknown machine '{machine}'."

    matches = _multi[machine].find(query=query)
    if not matches:
        return "No matching elements found."

    lines = [f"# {len(matches)} match{'es' if len(matches) != 1 else ''}:", ""]
    for m in matches:
        eid = m.get("id", "?")
        role = m.get("role", "")
        name = m.get("name", "")
        bounds = m.get("bounds")
        pos = ""
        if bounds:
            pos = f" @{bounds['x']},{bounds['y']} {bounds['w']}x{bounds['h']}"
        states = m.get("states", [])
        state_str = f" {{{','.join(states)}}}" if states else ""
        actions = m.get("actions", [])
        action_str = f" [{','.join(actions)}]" if actions else ""
        lines.append(f'[{eid}] {role} "{name}"{pos}{state_str}{action_str}')

    return "\n".join(lines)


@mcp.tool()
def open_app_on_machine(machine: str, app_name: str) -> str:
    """Open an application by name on a specific machine.

    Fuzzy-matches against installed apps (e.g., "chrome" finds Google Chrome,
    "code" finds Visual Studio Code). Waits for the app window to appear.

    After opening, use snapshot_machine to see the new app's UI.

    Args:
        machine: Machine name.
        app_name: Application name (fuzzy matched).
    """
    if machine not in _multi:
        return json.dumps({"success": False, "error": f"Unknown machine '{machine}'"})

    result = _multi[machine].open_app(app_name)
    return json.dumps({"success": result.success, "message": result.message, "error": result.error})


@mcp.tool()
def snapshot_all(scope: str = "overview") -> str:
    """Capture snapshots from ALL connected machines at once.

    Runs in parallel for speed. Useful to get a quick picture of what's
    happening across all machines.

    Args:
        scope: "overview" (default, just window lists) or "foreground" (active window trees).
    """
    trees = _multi.snapshot_all(scope=scope)
    parts = []
    for name, tree in trees.items():
        session = _multi[name]
        header = f"=== {name} ({session.os}, {session.machine}) ==="
        parts.append(f"{header}\n{tree}")
    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    mcp.run(transport="stdio")
