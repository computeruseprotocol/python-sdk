# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/), and this project adheres to [Semantic Versioning](https://semver.org/).

## [0.1.0] - 2026-02-23

Initial release. Extracted from [computeruseprotocol/computeruseprotocol](https://github.com/computeruseprotocol/computeruseprotocol).

### Added
- **Platform adapters** for tree capture:
  - Windows (UIA COM via comtypes)
  - macOS (AXUIElement via pyobjc)
  - Linux (AT-SPI2 via PyGObject)
  - Web (Chrome DevTools Protocol)
- **Action execution** on all four platforms (Windows, macOS, Linux, Web)
- **MCP server** (`cup-mcp`) with 8 tools for AI agent integration
- **Semantic search engine** with fuzzy matching, role synonyms, and relevance ranking
- **Viewport-aware pruning** that clips offscreen nodes using nested scrollable container intersection
- **Session API** with `snapshot()`, `action()`, `press()`, `find()`, `batch()`, and `screenshot()`
- **CLI** (`python -m cup`) for tree capture, JSON export, and compact output
- **CI** with GitHub Actions running tests on Windows, macOS, and Linux

[0.1.0]: https://github.com/computeruseprotocol/python-sdk/releases/tag/v0.1.0
