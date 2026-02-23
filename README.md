<p align="center">
  <a href="https://computeruseprotocol.com">
    <img src="assets/banner.png" alt="Computer Use Protocol">
  </a>
</p>

<p align="center">
  <b>Python SDK for the Computer Use Protocol</b>
</p>

<br>

<p align="center">
  <a href="https://pypi.org/project/computer-use-protocol"><img src="https://img.shields.io/pypi/v/computer-use-protocol?style=for-the-badge&color=FF6F61&labelColor=000000" alt="PyPI"></a>
  <a href="https://github.com/computeruseprotocol/python-sdk/blob/main/LICENSE"><img src="https://img.shields.io/badge/License-MIT-0cc0df?style=for-the-badge&labelColor=000000" alt="MIT License"></a>
  <a href="https://github.com/computeruseprotocol/computer-use-protocol"><img src="https://img.shields.io/badge/Spec-computer--use--protocol-7ed957?style=for-the-badge&labelColor=000000" alt="Spec"></a>
</p>

The official Python SDK for the [Computer Use Protocol (CUP)](https://github.com/computeruseprotocol/computer-use-protocol) — a universal protocol for AI agents to perceive and interact with any desktop UI. This package provides tree capture, action execution, semantic search, and an MCP server for AI agent integration.

## Installation

```bash
pip install computer-use-protocol

# Linux additionally requires system packages
sudo apt install python3-gi gir1.2-atspi-2.0

# Web adapter (Chrome DevTools Protocol, works on any OS)
pip install computer-use-protocol[web]

# MCP server for AI agent integration
pip install computer-use-protocol[mcp]
```

## Quick start

```python
import cup

# Snapshot the foreground window — optimized for LLM context windows
screen = cup.snapshot()
print(screen)

# All windows
screen = cup.snapshot("full")

# Structured CUP envelope (dict) instead of compact text
envelope = cup.snapshot_raw()
```

Output (compact format):

```
# CUP 0.1.0 | windows | 2560x1440
# app: Discord
# 87 nodes (353 before pruning)

[e0] window "Discord" @509,62 1992x1274
    [e1] document "General | Lechownia" @509,62 1992x1274 {readonly}
        [e2] button "Back" @518,66 26x24 [click]
        [e3] button "Forward" @546,66 26x24 {disabled} [click]
        [e7] tree "Servers" @509,94 72x1242
            [e8] treeitem "Lechownia" @513,190 64x48 {selected} [click,select]
```

## CLI

```bash
# Print compact tree of the foreground window
python -m cup --foreground --compact

# Save full JSON envelope
python -m cup --json-out tree.json

# Filter by app name
python -m cup --app Discord --compact

# Capture from Chrome via CDP
python -m cup --platform web --cdp-port 9222 --compact
```

## Platform support

| Platform | Adapter | Tree Capture | Actions |
|----------|---------|-------------|---------|
| Windows | UIA COM (comtypes) | Stable | Stable |
| macOS | AXUIElement (pyobjc) | Stable | Stable |
| Linux | AT-SPI2 (PyGObject) | Stable | Stable |
| Web | Chrome DevTools Protocol | Stable | Stable |
| Android | | Planned | Planned |
| iOS | | Planned | Planned |

CUP auto-detects your platform. Platform-specific dependencies (comtypes on Windows, pyobjc on macOS) are installed automatically.

## Architecture

```
cup/
├── __init__.py                 # Public API: snapshot, action, find, ...
├── __main__.py                 # CLI entry point
├── _base.py                    # Abstract PlatformAdapter interface
├── _router.py                  # Platform detection & adapter dispatch
├── format.py                   # Envelope builder, compact serializer, tree pruning
├── search.py                   # Semantic element search with fuzzy matching
├── actions/                    # Action execution layer
│   ├── executor.py             # ActionExecutor orchestrator
│   ├── _windows.py             # Windows UIA actions
│   ├── _web.py                 # Chrome CDP actions
│   ├── _macos.py               # macOS actions (Quartz CGEvents + AX)
│   └── _linux.py               # Linux actions (XTest + AT-SPI2)
├── platforms/                  # Platform-specific tree capture
│   ├── windows.py              # Windows UIA adapter
│   ├── macos.py                # macOS AXUIElement adapter
│   ├── linux.py                # Linux AT-SPI2 adapter
│   └── web.py                  # Chrome CDP adapter
└── mcp/                        # MCP server integration
    └── server.py               # MCP protocol server
```

Adding a new platform means implementing `PlatformAdapter` — see [cup/_base.py](cup/_base.py) for the interface.

## MCP Server

CUP ships an MCP server for integration with AI agents (Claude, Copilot, etc.).

```bash
# Run directly
cup-mcp

# Or via Python
python -m cup.mcp
```

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

**Tools:** `snapshot`, `snapshot_app`, `overview`, `snapshot_desktop`, `find`, `action`, `open_app`, `screenshot`

## Documentation

- **[API Reference](docs/api-reference.md)** — Session API, actions, envelope format, MCP server
- **[Protocol Specification](https://github.com/computeruseprotocol/computer-use-protocol)** — Schema, roles, states, actions, compact format

## Contributing

CUP is in early development (v0.1.0). Contributions welcome — especially:

- Android adapter (`cup/platforms/android.py`)
- iOS adapter (`cup/platforms/ios.py`)
- Tests and CI across platforms

For protocol or schema changes, please contribute to [computer-use-protocol](https://github.com/computeruseprotocol/computer-use-protocol).

See [CONTRIBUTING.md](CONTRIBUTING.md) for setup instructions and guidelines.

## License

[MIT](LICENSE)
