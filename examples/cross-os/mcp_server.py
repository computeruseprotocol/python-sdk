"""CUP Cross-OS MCP Server — control multiple machines from Claude Code.

Each connected machine is a numbered "screen" (1, 2, 3...). Every tool
accepts a ``screen`` parameter that can be the number or the friendly name.

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
from mcp.server.fastmcp.utilities.types import Image

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

# Build screen numbering (1-based, in argv order)
_screen_by_number: dict[int, str] = {}  # 1 -> "windows"
_screen_by_name: dict[str, int] = {}    # "windows" -> 1
for i, name in enumerate(_machine_specs.keys(), start=1):
    _screen_by_number[i] = name
    _screen_by_name[name] = i

_screen_list = ", ".join(
    f"screen {_screen_by_name[name]}={name} ({info['os']}, {info['machine']})"
    for name, info in _infos.items()
)

# ---------------------------------------------------------------------------
# Screen resolver
# ---------------------------------------------------------------------------


def _resolve_screen(screen: str | int) -> RemoteSession:
    """Resolve a screen identifier to a RemoteSession.

    Accepts: screen number (1, "1"), or friendly name ("windows", "mac").
    """
    # Try as number first
    try:
        num = int(screen)
        if num in _screen_by_number:
            return _multi[_screen_by_number[num]]
        raise ValueError(f"Screen {num} not found. Available: {list(_screen_by_number.keys())}")
    except (ValueError, TypeError):
        pass

    # Try as name
    key = str(screen).strip().lower()
    for name in _multi.sessions:
        if name.lower() == key:
            return _multi[name]

    available = ", ".join(
        f"{num}={name}" for num, name in _screen_by_number.items()
    )
    raise ValueError(f"Unknown screen '{screen}'. Available screens: {available}")


def _screen_label(screen: str | int) -> str:
    """Return a human-readable label for a screen."""
    try:
        num = int(screen)
        if num in _screen_by_number:
            name = _screen_by_number[num]
            s = _multi[name]
            return f"screen {num} ({name}, {s.os})"
    except (ValueError, TypeError):
        pass
    key = str(screen).strip().lower()
    for name in _multi.sessions:
        if name.lower() == key:
            num = _screen_by_name[name]
            s = _multi[name]
            return f"screen {num} ({name}, {s.os})"
    return f"screen '{screen}'"


# ---------------------------------------------------------------------------
# MCP Server
# ---------------------------------------------------------------------------

mcp = FastMCP(
    name="cup-cross-os",
    instructions=(
        "CUP Cross-OS — control multiple machines through their UI.\n\n"
        f"Connected screens: {_screen_list}\n\n"
        "Each machine is a numbered 'screen'. Use the screen number or name "
        "in every tool call.\n\n"
        "WORKFLOW:\n"
        "1. list_screens to see connected machines\n"
        "2. snapshot(screen) to capture the active window's UI\n"
        "3. find(screen, ...) to locate specific elements (avoids re-capturing)\n"
        "4. action(screen, ...) to interact (click, type, press, etc.)\n"
        "5. Re-snapshot ONLY after actions change the UI\n\n"
        "CUP FORMAT — each line is a UI element:\n"
        "  [id] role \"name\" @x,y wxh {states} [actions] val=\"value\"\n"
        "Element IDs (e.g., 'e14') are ephemeral — only valid for the most "
        "recent snapshot of THAT screen. After any action, re-snapshot.\n\n"
        "The tree format is IDENTICAL across all OSes — that's the point of CUP.\n\n"
        "IMPORTANT — minimize token usage:\n"
        "- Use find(screen, name=...) to locate elements — NOT repeated snapshots\n"
        "- Use overview(screen) to discover what apps are open\n"
        "- Use snapshot_app(screen, app) to target a specific app\n"
        "- Use screenshot(screen) when you need visual context\n"
    ),
)

# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@mcp.tool()
def list_screens() -> str:
    """List all connected screens with their number, name, OS, and hostname.

    Call this first to see what screens you can control.
    Each screen can be referenced by its number (1, 2, 3...) or name.
    """
    lines = []
    for num, name in _screen_by_number.items():
        session = _multi[name]
        lines.append(
            f"- **Screen {num}** ({name}): {session.machine} "
            f"(os={session.os}, platform={session.platform_name})"
        )
    return "\n".join(lines)


@mcp.tool()
def snapshot(screen: str, scope: str = "foreground") -> str:
    """Capture the UI accessibility tree from a screen.

    Returns a structured text representation where each UI element has an ID
    (e.g., 'e14') that can be used with the action tool.

    Also includes a window list header showing all open apps on that screen.

    Element IDs are ephemeral — only valid for THIS snapshot of THIS screen.
    After executing any action, re-snapshot for fresh IDs.

    Args:
        screen: Screen number (1, 2, ...) or name (e.g., "windows", "mac").
        scope: What to capture — "foreground" (default) or "overview" (just window list).
    """
    try:
        session = _resolve_screen(screen)
    except ValueError as e:
        return str(e)
    result = session.snapshot(scope=scope, compact=True)
    return result if isinstance(result, str) else json.dumps(result)


@mcp.tool()
def snapshot_app(screen: str, app: str) -> str:
    """Capture a specific app's window accessibility tree on a screen.

    Use this when you need to interact with a window that is NOT in the
    foreground, or when you know the exact app you want.

    The 'app' parameter is a case-insensitive substring match against
    window titles (e.g., "Spotify", "Firefox", "VS Code").

    Element IDs are ephemeral — only valid for THIS snapshot.

    Args:
        screen: Screen number or name.
        app: Target app by window title (case-insensitive substring match).
    """
    try:
        session = _resolve_screen(screen)
    except ValueError as e:
        return str(e)
    result = session.snapshot(scope="full", app=app, compact=True)
    return result if isinstance(result, str) else json.dumps(result)


@mcp.tool()
def snapshot_desktop(screen: str) -> str:
    """Capture the desktop surface (icons, widgets, shortcuts) on a screen.

    Falls back to a window overview if the platform has no desktop concept.

    Element IDs are ephemeral — only valid for THIS snapshot.

    Args:
        screen: Screen number or name.
    """
    try:
        session = _resolve_screen(screen)
    except ValueError as e:
        return str(e)
    result = session.snapshot_desktop(compact=True)
    return result if isinstance(result, str) else json.dumps(result)


@mcp.tool()
def overview(screen: str) -> str:
    """List all open windows on a screen. Near-instant, no tree walking.

    Returns a lightweight window list showing app names and status.
    No element IDs are returned.

    Use this to quickly discover what apps are open before targeting
    a specific one with snapshot_app.

    Args:
        screen: Screen number or name.
    """
    try:
        session = _resolve_screen(screen)
    except ValueError as e:
        return str(e)
    return session.overview()


@mcp.tool()
def action(
    screen: str,
    action: str,
    element_id: str | None = None,
    value: str | None = None,
    direction: str | None = None,
    keys: str | None = None,
) -> str:
    """Perform a UI action on an element on a specific screen.

    Element IDs come from the most recent snapshot of THAT screen.
    After performing an action, re-snapshot for fresh IDs.

    Element actions (require element_id):
        click      — Click/invoke the element
        rightclick — Right-click to open context menu
        doubleclick— Double-click the element
        toggle     — Toggle a checkbox or switch
        type       — Type text into a text field (pass text in 'value')
        setvalue   — Set element value programmatically (pass in 'value')
        select     — Select an item in a list/tree/tab
        expand     — Expand a collapsed element
        collapse   — Collapse an expanded element
        scroll     — Scroll a container (pass direction: up/down/left/right)
        increment  — Increment a slider/spinbutton
        decrement  — Decrement a slider/spinbutton
        focus      — Move keyboard focus to the element

    Keyboard shortcut (no element_id needed):
        press      — Send a keyboard shortcut (pass combo in 'keys')
                     Examples: "enter", "ctrl+s", "ctrl+shift+p", "alt+f4"

    Args:
        screen: Screen number or name.
        action: The action to perform.
        element_id: Element ID from the tree (e.g., "e14"). Required for
                    all actions except press.
        value: Text for 'type' or 'setvalue' actions.
        direction: Direction for 'scroll' action (up/down/left/right).
        keys: Key combination for 'press' action (e.g., "ctrl+s").
    """
    try:
        session = _resolve_screen(screen)
    except ValueError as e:
        return json.dumps({"success": False, "error": str(e)})

    if action == "press":
        if not keys:
            return json.dumps({
                "success": False,
                "error": "press action requires the 'keys' parameter (e.g., keys='ctrl+s').",
            })
        result = session.press(keys)
        return json.dumps({"success": result.success, "message": result.message, "error": result.error})

    if not element_id:
        return json.dumps({
            "success": False,
            "error": f"Action '{action}' requires the 'element_id' parameter.",
        })

    params: dict = {}
    if value is not None:
        params["value"] = value
    if direction is not None:
        params["direction"] = direction

    result = session.action(element_id, action, **params)
    return json.dumps({"success": result.success, "message": result.message, "error": result.error})


@mcp.tool()
def find(
    screen: str,
    query: str | None = None,
    role: str | None = None,
    name: str | None = None,
    state: str | None = None,
) -> str:
    """Search the last captured tree on a screen for elements matching criteria.

    Searches the FULL tree (including elements not shown in compact output)
    with semantic matching and relevance ranking.

    QUERY MODE (recommended):
        Pass a freeform ``query`` describing what you're looking for.

        Examples:
            query="the play button"     -> finds buttons with "play" in the name
            query="search input"        -> finds textbox/combobox/searchbox elements
            query="volume slider"       -> finds sliders with "volume" in the name

    STRUCTURED MODE:
        Pass explicit role, name, and/or state filters.

        role  — CUP role or natural language (e.g., "button", "search bar")
        name  — Fuzzy name match (token overlap)
        state — Exact state match (e.g., "focused", "disabled", "checked")

    Both modes can be combined: query + state="focused" narrows to focused elements.

    Args:
        screen: Screen number or name.
        query: Freeform semantic query (e.g., "play button", "search input").
        role: Filter by role (exact CUP role or natural language synonym).
        name: Filter by name (fuzzy token matching).
        state: Filter by state (exact match).
    """
    if query is None and role is None and name is None and state is None:
        return json.dumps({
            "success": False,
            "error": "At least one search parameter (query, role, name, or state) must be provided.",
        })

    try:
        session = _resolve_screen(screen)
    except ValueError as e:
        return str(e)

    matches = session.find(query=query, role=role, name=name, state=state)
    if not matches:
        return "No matching elements found."

    lines = [f"# {len(matches)} match{'es' if len(matches) != 1 else ''} found", ""]
    for m in matches:
        eid = m.get("id", "?")
        mrole = m.get("role", "")
        mname = m.get("name", "")
        bounds = m.get("bounds")
        pos = ""
        if bounds:
            pos = f" @{bounds['x']},{bounds['y']} {bounds['w']}x{bounds['h']}"
        states = m.get("states", [])
        state_str = f" {{{','.join(states)}}}" if states else ""
        actions = m.get("actions", [])
        action_str = f" [{','.join(actions)}]" if actions else ""
        lines.append(f'[{eid}] {mrole} "{mname}"{pos}{state_str}{action_str}')

    return "\n".join(lines) + "\n"


@mcp.tool()
def open_app(screen: str, app_name: str) -> str:
    """Open an application by name on a specific screen.

    Fuzzy-matches against installed apps (e.g., "chrome" finds Google Chrome,
    "code" finds Visual Studio Code). Waits for the app window to appear.

    After opening, use snapshot(screen) to capture the new app's UI.

    Args:
        screen: Screen number or name.
        app_name: Application name (fuzzy matched).
    """
    try:
        session = _resolve_screen(screen)
    except ValueError as e:
        return json.dumps({"success": False, "error": str(e)})

    result = session.open_app(app_name)
    return json.dumps({"success": result.success, "message": result.message, "error": result.error})


@mcp.tool()
def screenshot(
    screen: str,
    region_x: int | None = None,
    region_y: int | None = None,
    region_w: int | None = None,
    region_h: int | None = None,
) -> Image | str:
    """Capture a screenshot from a specific screen and return it as a PNG image.

    By default captures the full primary monitor on the target machine.
    Optionally specify a region to capture only part of the screen.

    Use this alongside tree capture tools when you need visual context
    (e.g., to see colors, images, or layout that the tree doesn't capture).

    Args:
        screen: Screen number or name.
        region_x: Left edge of capture region in pixels.
        region_y: Top edge of capture region in pixels.
        region_w: Width of capture region in pixels.
        region_h: Height of capture region in pixels.
    """
    try:
        session = _resolve_screen(screen)
    except ValueError as e:
        return json.dumps({"success": False, "error": str(e)})

    region_params = [region_x, region_y, region_w, region_h]
    has_any = any(v is not None for v in region_params)
    has_all = all(v is not None for v in region_params)

    if has_any and not has_all:
        return json.dumps({
            "success": False,
            "error": "All region parameters (region_x, region_y, region_w, region_h) "
                     "must be provided together, or none at all.",
        })

    region = None
    if has_all:
        region = {"x": region_x, "y": region_y, "w": region_w, "h": region_h}

    try:
        png_bytes = session.screenshot(region=region)
    except (ImportError, RuntimeError) as e:
        return json.dumps({"success": False, "error": str(e)})

    return Image(data=png_bytes, format="png")


@mcp.tool()
def snapshot_all(scope: str = "overview") -> str:
    """Capture snapshots from ALL connected screens at once.

    Runs in parallel for speed. Useful to get a quick picture of what's
    happening across all machines.

    Args:
        scope: "overview" (default, just window lists) or "foreground" (active window trees).
    """
    trees = _multi.snapshot_all(scope=scope)
    parts = []
    for name, tree in trees.items():
        num = _screen_by_name[name]
        session = _multi[name]
        header = f"=== Screen {num}: {name} ({session.os}, {session.machine}) ==="
        parts.append(f"{header}\n{tree}")
    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    mcp.run(transport="stdio")
