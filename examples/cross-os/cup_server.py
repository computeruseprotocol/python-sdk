"""CUP Remote Server — exposes a local cup.Session() over WebSocket.

Run this on each machine you want to control:

    python cup_server.py                    # default port 9800
    python cup_server.py --port 9801        # custom port
    python cup_server.py --host 0.0.0.0     # listen on all interfaces

The server speaks a simple JSON-RPC-like protocol over WebSocket:

    -> {"id": 1, "method": "snapshot", "params": {"scope": "foreground"}}
    <- {"id": 1, "result": "# CUP 0.1.0 | windows | ..."}

    -> {"id": 2, "method": "action", "params": {"element_id": "e5", "action": "click"}}
    <- {"id": 2, "result": {"success": true, "message": "Clicked"}}

Supported methods: snapshot, snapshot_desktop, action, press, find, overview,
                   open_app, screenshot, batch, info
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import platform
import sys
from typing import Any

# The cup SDK must be installed: pip install computeruseprotocol
import cup
from cup.actions.executor import ActionResult

try:
    import websockets
    import websockets.asyncio.server
except ImportError:
    print("This server requires the 'websockets' package.")
    print("Install it with:  pip install websockets")
    sys.exit(1)


class CupRpcServer:
    """Wraps a cup.Session and dispatches JSON-RPC calls."""

    def __init__(self) -> None:
        self._session = cup.Session()
        self._machine = platform.node() or "unknown"
        self._os = platform.system().lower()

    # -- RPC dispatch -------------------------------------------------------

    def dispatch(self, method: str, params: dict[str, Any]) -> Any:
        handler = getattr(self, f"rpc_{method}", None)
        if handler is None:
            raise ValueError(f"Unknown method: {method}")
        return handler(**params)

    # -- Methods ------------------------------------------------------------

    def rpc_info(self) -> dict:
        """Return machine metadata."""
        return {
            "machine": self._machine,
            "os": self._os,
            "platform": self._session._adapter.platform_name,
            "python": platform.python_version(),
        }

    def rpc_snapshot(
        self,
        scope: str = "foreground",
        app: str | None = None,
        compact: bool = True,
    ) -> str | dict:
        return self._session.snapshot(scope=scope, app=app, compact=compact)

    def rpc_overview(self) -> str:
        return self._session.snapshot(scope="overview", compact=True)

    def rpc_action(
        self,
        element_id: str,
        action: str,
        **params: Any,
    ) -> dict:
        result: ActionResult = self._session.action(element_id, action, **params)
        return {"success": result.success, "message": result.message, "error": result.error}

    def rpc_press(self, keys: str) -> dict:
        result = self._session.press(keys)
        return {"success": result.success, "message": result.message, "error": result.error}

    def rpc_find(
        self,
        query: str | None = None,
        role: str | None = None,
        name: str | None = None,
        state: str | None = None,
        limit: int = 5,
    ) -> list[dict]:
        return self._session.find(query=query, role=role, name=name, state=state, limit=limit)

    def rpc_open_app(self, name: str) -> dict:
        result = self._session.open_app(name)
        return {"success": result.success, "message": result.message, "error": result.error}

    def rpc_snapshot_desktop(self, compact: bool = True) -> str | dict:
        return self._session.snapshot(scope="desktop", compact=compact)

    def rpc_screenshot(
        self,
        region_x: int | None = None,
        region_y: int | None = None,
        region_w: int | None = None,
        region_h: int | None = None,
    ) -> dict:
        """Capture screenshot and return as base64-encoded PNG."""
        region = None
        if all(v is not None for v in (region_x, region_y, region_w, region_h)):
            region = {"x": region_x, "y": region_y, "w": region_w, "h": region_h}
        try:
            png_bytes = self._session.screenshot(region=region)
            return {"success": True, "data": base64.b64encode(png_bytes).decode("ascii")}
        except (ImportError, RuntimeError) as e:
            return {"success": False, "error": str(e)}

    def rpc_batch(self, actions: list[dict]) -> list[dict]:
        results = self._session.batch(actions)
        return [{"success": r.success, "message": r.message, "error": r.error} for r in results]


async def handle_client(
    rpc: CupRpcServer,
    websocket: websockets.asyncio.server.ServerConnection,
) -> None:
    """Handle a single WebSocket client connection."""
    info = rpc.rpc_info()
    print(f"  Client connected from {websocket.remote_address}")

    async for raw_message in websocket:
        try:
            msg = json.loads(raw_message)
        except json.JSONDecodeError as e:
            await websocket.send(json.dumps({"error": f"Invalid JSON: {e}"}))
            continue

        msg_id = msg.get("id")
        method = msg.get("method", "")
        params = msg.get("params", {})

        try:
            # Run synchronous CUP calls in a thread to avoid blocking
            result = await asyncio.to_thread(rpc.dispatch, method, params)
            response = {"id": msg_id, "result": result}
        except Exception as e:
            response = {"id": msg_id, "error": str(e)}

        await websocket.send(json.dumps(response, default=str))

    print(f"  Client disconnected: {websocket.remote_address}")


async def main(host: str, port: int) -> None:
    rpc = CupRpcServer()
    info = rpc.rpc_info()

    print(f"CUP Remote Server")
    print(f"  Machine:  {info['machine']}")
    print(f"  OS:       {info['os']}")
    print(f"  Platform: {info['platform']}")
    print(f"  Listening on ws://{host}:{port}")
    print()

    async with websockets.asyncio.server.serve(
        lambda ws: handle_client(rpc, ws),
        host,
        port,
    ):
        await asyncio.Future()  # run forever


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="CUP Remote Server")
    parser.add_argument("--host", default="0.0.0.0", help="Bind address (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=9800, help="Port (default: 9800)")
    args = parser.parse_args()

    try:
        asyncio.run(main(args.host, args.port))
    except KeyboardInterrupt:
        print("\nShutdown.")
