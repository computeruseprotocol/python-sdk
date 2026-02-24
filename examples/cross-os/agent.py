"""Cross-OS Agent — AI-driven UI automation across multiple machines.

Connects to cup_server.py instances running on different OS machines and
uses Claude to coordinate tasks across all of them. The agent sees CUP
trees from every machine in a unified format, regardless of OS.

Usage:

    # 1. Start cup_server.py on each machine:
    #    Windows PC:  python cup_server.py --port 9800
    #    Linux box:   python cup_server.py --port 9800
    #    Mac:         python cup_server.py --port 9800

    # 2. Run the agent from any machine:
    python agent.py windows=ws://192.168.1.10:9800 linux=ws://192.168.1.20:9800

    # Or with explicit task:
    python agent.py windows=ws://10.0.0.5:9800 linux=ws://10.0.0.6:9800 \\
        --task "Open Notepad on Windows and type 'Hello from Linux', then open gedit on Linux and type 'Hello from Windows'"

Requires:
    pip install anthropic websocket-client
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

try:
    import anthropic
except ImportError:
    print("This agent requires the 'anthropic' package.")
    print("Install it with:  pip install anthropic")
    sys.exit(1)

from cup_remote import MultiSession

# ---------------------------------------------------------------------------
# System prompt — teaches the agent about CUP + multi-machine control
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """\
You are a cross-OS UI automation agent. You control multiple machines \
simultaneously, each running a different operating system. All machines \
expose their UI through the Computer Use Protocol (CUP), so you see a \
unified accessibility tree format regardless of whether a machine runs \
Windows, macOS, or Linux.

## Your machines

{machine_descriptions}

## CUP Compact Format

Each line represents a UI element:
  [id] role "name" @x,y wxh {{states}} [actions] val="value"

- [id]: Element identifier (e.g., "e14"). Pass just the ID string to tools.
- role: Semantic type — button, textbox, menuitem, tab, treeitem, etc.
- "name": The element's accessible label.
- @x,y wxh: Position and size in pixels.
- {{states}}: Active states — disabled, focused, checked, expanded, etc.
- [actions]: Available actions — click, toggle, type, select, expand, etc.

Hierarchy is shown by 2-space indentation.

## Rules

1. Use snapshot_machine to see a machine's current UI.
2. Use act_on_machine to perform actions on elements.
3. Element IDs are ephemeral — after any action, re-snapshot that machine.
4. You can act on different machines in sequence to coordinate cross-OS tasks.
5. The CUP format is identical across all OS — that's the whole point.
6. Call task_complete when done.
7. Be concise in your reasoning.\
"""

# ---------------------------------------------------------------------------
# Tool definitions for Claude
# ---------------------------------------------------------------------------

TOOLS = [
    {
        "name": "snapshot_machine",
        "description": (
            "Capture the UI accessibility tree from a specific machine. "
            "Returns the CUP compact format showing all visible elements."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "machine": {
                    "type": "string",
                    "description": "Machine name (as shown in 'Your machines' above)",
                },
                "scope": {
                    "type": "string",
                    "enum": ["foreground", "full", "overview"],
                    "description": "What to capture. 'foreground' = active window (default), "
                    "'full' = all windows, 'overview' = just the window list.",
                },
                "app": {
                    "type": "string",
                    "description": "Filter to a specific app by title (only with scope='full').",
                },
            },
            "required": ["machine"],
        },
    },
    {
        "name": "act_on_machine",
        "description": (
            "Perform a UI action on a specific machine. Element IDs come from "
            "the most recent snapshot of that machine."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "machine": {
                    "type": "string",
                    "description": "Machine name",
                },
                "element_id": {
                    "type": "string",
                    "description": "Element ID from the tree (e.g., 'e14')",
                },
                "action": {
                    "type": "string",
                    "enum": [
                        "click", "type", "toggle", "select", "expand",
                        "collapse", "scroll", "press", "setvalue",
                        "doubleclick", "rightclick",
                    ],
                    "description": "Action to perform",
                },
                "value": {
                    "type": "string",
                    "description": "Text for 'type'/'setvalue' actions",
                },
                "direction": {
                    "type": "string",
                    "enum": ["up", "down", "left", "right"],
                    "description": "Direction for 'scroll' action",
                },
                "keys": {
                    "type": "string",
                    "description": "Key combo for 'press' action (e.g., 'ctrl+s')",
                },
            },
            "required": ["machine", "action"],
        },
    },
    {
        "name": "open_app_on_machine",
        "description": "Open an application by name on a specific machine.",
        "input_schema": {
            "type": "object",
            "properties": {
                "machine": {
                    "type": "string",
                    "description": "Machine name",
                },
                "app_name": {
                    "type": "string",
                    "description": "Application name (fuzzy matched, e.g., 'notepad', 'chrome')",
                },
            },
            "required": ["machine", "app_name"],
        },
    },
    {
        "name": "find_on_machine",
        "description": (
            "Search the last captured tree on a machine for elements matching "
            "a query. Avoids re-capturing the full tree."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "machine": {
                    "type": "string",
                    "description": "Machine name",
                },
                "query": {
                    "type": "string",
                    "description": "Freeform query (e.g., 'submit button', 'search input')",
                },
            },
            "required": ["machine", "query"],
        },
    },
    {
        "name": "task_complete",
        "description": "Signal that the cross-OS task is finished.",
        "input_schema": {
            "type": "object",
            "properties": {
                "summary": {
                    "type": "string",
                    "description": "Brief summary of what was accomplished across machines.",
                },
            },
            "required": ["summary"],
        },
    },
]

# ---------------------------------------------------------------------------
# Tool execution
# ---------------------------------------------------------------------------


def handle_tool(multi: MultiSession, name: str, inp: dict) -> str:
    """Execute a tool call and return the result as a string."""

    if name == "task_complete":
        return json.dumps({"status": "complete", "summary": inp.get("summary", "")})

    machine_name = inp.get("machine", "")
    if machine_name not in multi:
        return json.dumps({
            "error": f"Unknown machine '{machine_name}'. Available: {list(multi.sessions.keys())}"
        })

    session = multi[machine_name]

    if name == "snapshot_machine":
        scope = inp.get("scope", "foreground")
        app = inp.get("app")
        tree = session.snapshot(scope=scope, app=app, compact=True)
        return tree if isinstance(tree, str) else json.dumps(tree)

    elif name == "act_on_machine":
        action = inp["action"]

        # press doesn't need element_id
        if action == "press":
            keys = inp.get("keys", "")
            if not keys:
                return json.dumps({"error": "press action requires 'keys'"})
            result = session.press(keys)
        else:
            element_id = inp.get("element_id", "")
            if not element_id:
                return json.dumps({"error": f"Action '{action}' requires 'element_id'"})

            params = {}
            if "value" in inp:
                params["value"] = inp["value"]
            if "direction" in inp:
                params["direction"] = inp["direction"]

            result = session.action(element_id, action, **params)

        return json.dumps({
            "success": result.success,
            "message": result.message,
            "error": result.error,
        })

    elif name == "open_app_on_machine":
        result = session.open_app(inp["app_name"])
        return json.dumps({
            "success": result.success,
            "message": result.message,
            "error": result.error,
        })

    elif name == "find_on_machine":
        matches = session.find(query=inp.get("query"))
        if not matches:
            return "No matching elements found."
        # Format matches in CUP-like compact lines
        lines = []
        for m in matches:
            eid = m.get("id", "?")
            role = m.get("role", "")
            mname = m.get("name", "")
            lines.append(f'[{eid}] {role} "{mname}"')
        return "\n".join(lines)

    return json.dumps({"error": f"Unknown tool: {name}"})


# ---------------------------------------------------------------------------
# Agent loop
# ---------------------------------------------------------------------------


def run_agent(multi: MultiSession, task: str, model: str = "claude-sonnet-4-20250514") -> None:
    """Run the cross-OS agent loop with Claude."""

    client = anthropic.Anthropic()

    # Build machine descriptions for the system prompt
    descriptions = []
    for name, session in multi.sessions.items():
        descriptions.append(
            f"- **{name}**: {session.machine} ({session.os}, platform={session.platform_name})"
        )

    system = SYSTEM_PROMPT.format(machine_descriptions="\n".join(descriptions))

    messages = [{"role": "user", "content": task}]

    print(f"\nTask: {task}")
    print(f"Machines: {multi}")
    print(f"Model: {model}")
    print("-" * 60)

    step = 0
    while True:
        step += 1

        response = client.messages.create(
            model=model,
            max_tokens=4096,
            system=system,
            tools=TOOLS,
            messages=messages,
        )

        # Process response blocks
        assistant_content = response.content
        messages.append({"role": "assistant", "content": assistant_content})

        tool_results = []
        done = False

        for block in assistant_content:
            if block.type == "text" and block.text.strip():
                print(f"\nAgent> {block.text}")

            elif block.type == "tool_use":
                tool_name = block.name
                tool_input = block.input
                tool_id = block.id

                # Log the tool call
                machine = tool_input.get("machine", "")
                if tool_name == "snapshot_machine":
                    scope = tool_input.get("scope", "foreground")
                    print(f"\n  [{step}] {tool_name}({machine}, scope={scope})")
                elif tool_name == "act_on_machine":
                    action = tool_input.get("action", "")
                    eid = tool_input.get("element_id", "")
                    extra = ""
                    if "value" in tool_input:
                        extra = f', value="{tool_input["value"]}"'
                    if "keys" in tool_input:
                        extra = f', keys="{tool_input["keys"]}"'
                    print(f"  [{step}] {tool_name}({machine}, {eid}.{action}{extra})")
                elif tool_name == "open_app_on_machine":
                    print(f"  [{step}] {tool_name}({machine}, {tool_input.get('app_name', '')})")
                elif tool_name == "find_on_machine":
                    print(f"  [{step}] {tool_name}({machine}, query={tool_input.get('query', '')!r})")
                elif tool_name == "task_complete":
                    print(f"\n  Done: {tool_input.get('summary', '')}")
                    done = True

                # Execute
                result_str = handle_tool(multi, tool_name, tool_input)
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tool_id,
                    "content": result_str,
                })

                # Brief status
                if tool_name == "act_on_machine":
                    try:
                        r = json.loads(result_str)
                        ok = r.get("success", False)
                        msg = r.get("message", r.get("error", ""))
                        print(f"       -> {'OK' if ok else 'FAIL'}: {msg}")
                    except json.JSONDecodeError:
                        pass

        if done:
            break

        if tool_results:
            messages.append({"role": "user", "content": tool_results})
        else:
            # No tool calls and no done signal — agent is just talking
            break

        # Safety: cap at 50 steps
        if step >= 50:
            print("\n  Reached step limit (50). Stopping.")
            break

        # Small delay between steps to let UI settle
        time.sleep(0.3)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Cross-OS Agent — AI UI automation across machines",
        usage="%(prog)s machine1=ws://host:port [machine2=ws://host:port ...] [--task TASK]",
    )
    parser.add_argument(
        "machines",
        nargs="+",
        metavar="NAME=URL",
        help="Machine connections as name=ws://host:port pairs",
    )
    parser.add_argument(
        "--task",
        help="Task to perform (if not provided, enters interactive mode)",
    )
    parser.add_argument(
        "--model",
        default="claude-sonnet-4-20250514",
        help="Claude model to use (default: claude-sonnet-4-20250514)",
    )
    args = parser.parse_args()

    # Parse machine=url pairs
    machines = {}
    for spec in args.machines:
        if "=" not in spec:
            parser.error(f"Invalid machine spec '{spec}'. Use format: name=ws://host:port")
        name, url = spec.split("=", 1)
        machines[name.strip()] = url.strip()

    if not machines:
        parser.error("At least one machine must be specified.")

    print("Cross-OS CUP Agent")
    print("=" * 40)
    print(f"Connecting to {len(machines)} machine(s)...")

    with MultiSession(machines) as multi:
        # Show connected machines
        for name, session in multi.sessions.items():
            print(f"  {name}: {session.machine} ({session.os}, {session.platform_name})")
        print()

        if args.task:
            run_agent(multi, args.task, model=args.model)
        else:
            # Interactive REPL
            print("Interactive mode. Type a task and press Enter.")
            print("Type 'quit' to exit, 'machines' to list connections.\n")

            while True:
                try:
                    task = input("Task> ").strip()
                except (EOFError, KeyboardInterrupt):
                    print("\nBye!")
                    break

                if not task:
                    continue
                if task.lower() in ("quit", "exit", "q"):
                    break
                if task.lower() == "machines":
                    for name, session in multi.sessions.items():
                        print(f"  {name}: {session}")
                    continue

                run_agent(multi, task, model=args.model)
                print()


if __name__ == "__main__":
    main()
