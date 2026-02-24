"""CUP Remote Client — connects to cup_server.py instances over WebSocket.

Provides a RemoteSession class that mirrors the cup.Session() API but talks
to a remote machine. Also provides MultiSession for coordinating across
multiple machines.

Usage:

    from cup_remote import RemoteSession, MultiSession

    # Single remote machine
    win = RemoteSession("ws://windows-pc:9800")
    tree = win.snapshot(scope="foreground")
    win.action("e5", "click")

    # Multiple machines
    multi = MultiSession({
        "windows": "ws://windows-pc:9800",
        "linux":   "ws://linux-box:9800",
    })
    multi.connect_all()
    trees = multi.snapshot_all(scope="foreground")
    # trees == {"windows": "# CUP 0.1.0 | windows ...", "linux": "# CUP 0.1.0 | linux ..."}
"""

from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any

try:
    from websocket import WebSocket, create_connection
except ImportError:
    raise ImportError(
        "cup_remote requires the 'websocket-client' package.\n"
        "Install it with:  pip install websocket-client"
    ) from None


@dataclass
class ActionResult:
    """Mirrors cup.actions.executor.ActionResult."""

    success: bool
    message: str
    error: str | None = None


class RemoteSession:
    """A CUP session that talks to a remote cup_server.py instance.

    Drop-in replacement for cup.Session() — same methods, but every call
    goes over the network to the target machine.
    """

    def __init__(self, url: str, *, timeout: float = 30.0) -> None:
        self.url = url
        self.timeout = timeout
        self._ws: WebSocket | None = None
        self._lock = threading.Lock()
        self._msg_id = 0
        self.info: dict | None = None

    def connect(self) -> dict:
        """Connect to the remote CUP server and return its info."""
        self._ws = create_connection(self.url, timeout=self.timeout)
        self.info = self._call("info")
        return self.info

    def close(self) -> None:
        if self._ws:
            self._ws.close()
            self._ws = None

    @property
    def machine(self) -> str:
        return (self.info or {}).get("machine", "unknown")

    @property
    def os(self) -> str:
        return (self.info or {}).get("os", "unknown")

    @property
    def platform_name(self) -> str:
        return (self.info or {}).get("platform", "unknown")

    # -- RPC helper ---------------------------------------------------------

    def _call(self, method: str, **params: Any) -> Any:
        if self._ws is None:
            raise RuntimeError("Not connected. Call .connect() first.")

        with self._lock:
            self._msg_id += 1
            msg_id = self._msg_id

            request = {"id": msg_id, "method": method, "params": params}
            self._ws.send(json.dumps(request))

            raw = self._ws.recv()
            response = json.loads(raw)

            if "error" in response and response["error"] is not None:
                raise RuntimeError(f"Remote error: {response['error']}")

            return response.get("result")

    # -- Session API (mirrors cup.Session) ----------------------------------

    def snapshot(
        self,
        *,
        scope: str = "foreground",
        app: str | None = None,
        compact: bool = True,
    ) -> str | dict:
        params: dict[str, Any] = {"scope": scope, "compact": compact}
        if app is not None:
            params["app"] = app
        return self._call("snapshot", **params)

    def overview(self) -> str:
        return self._call("overview")

    def action(self, element_id: str, action: str, **params: Any) -> ActionResult:
        result = self._call("action", element_id=element_id, action=action, **params)
        return ActionResult(**result)

    def press(self, keys: str) -> ActionResult:
        result = self._call("press", keys=keys)
        return ActionResult(**result)

    def find(
        self,
        *,
        query: str | None = None,
        role: str | None = None,
        name: str | None = None,
        state: str | None = None,
        limit: int = 5,
    ) -> list[dict]:
        return self._call("find", query=query, role=role, name=name, state=state, limit=limit)

    def open_app(self, name: str) -> ActionResult:
        result = self._call("open_app", name=name)
        return ActionResult(**result)

    def batch(self, actions: list[dict]) -> list[ActionResult]:
        results = self._call("batch", actions=actions)
        return [ActionResult(**r) for r in results]

    def __repr__(self) -> str:
        state = "connected" if self._ws else "disconnected"
        return f"RemoteSession({self.url!r}, {state}, machine={self.machine!r}, os={self.os!r})"

    def __enter__(self) -> RemoteSession:
        self.connect()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


class MultiSession:
    """Manages multiple RemoteSession instances for cross-OS orchestration.

    Usage:

        multi = MultiSession({
            "windows": "ws://192.168.1.10:9800",
            "linux":   "ws://192.168.1.20:9800",
            "mac":     "ws://192.168.1.30:9800",
        })
        multi.connect_all()

        # Snapshot all machines in parallel
        trees = multi.snapshot_all(scope="foreground")
        # -> {"windows": "# CUP ...", "linux": "# CUP ...", "mac": "# CUP ..."}

        # Target a specific machine
        multi["windows"].action("e5", "click")
        multi["linux"].action("e12", "type", value="hello from windows!")
    """

    def __init__(self, machines: dict[str, str]) -> None:
        """
        Args:
            machines: Mapping of friendly name -> WebSocket URL.
                      e.g. {"windows": "ws://192.168.1.10:9800"}
        """
        self.sessions: dict[str, RemoteSession] = {
            name: RemoteSession(url) for name, url in machines.items()
        }

    def connect_all(self) -> dict[str, dict]:
        """Connect to all machines in parallel. Returns {name: info}."""
        infos = {}
        with ThreadPoolExecutor(max_workers=len(self.sessions)) as pool:
            futures = {
                pool.submit(session.connect): name
                for name, session in self.sessions.items()
            }
            for future in as_completed(futures):
                name = futures[future]
                try:
                    infos[name] = future.result()
                except Exception as e:
                    raise ConnectionError(f"Failed to connect to '{name}': {e}") from e
        return infos

    def close_all(self) -> None:
        for session in self.sessions.values():
            session.close()

    def snapshot_all(self, **kwargs: Any) -> dict[str, str | dict]:
        """Capture snapshots from all machines in parallel."""
        results = {}
        with ThreadPoolExecutor(max_workers=len(self.sessions)) as pool:
            futures = {
                pool.submit(session.snapshot, **kwargs): name
                for name, session in self.sessions.items()
            }
            for future in as_completed(futures):
                name = futures[future]
                results[name] = future.result()
        return results

    def overview_all(self) -> dict[str, str]:
        """Get window overviews from all machines in parallel."""
        results = {}
        with ThreadPoolExecutor(max_workers=len(self.sessions)) as pool:
            futures = {
                pool.submit(session.overview): name
                for name, session in self.sessions.items()
            }
            for future in as_completed(futures):
                name = futures[future]
                results[name] = future.result()
        return results

    def __getitem__(self, name: str) -> RemoteSession:
        return self.sessions[name]

    def __contains__(self, name: str) -> bool:
        return name in self.sessions

    def __repr__(self) -> str:
        machines = ", ".join(
            f"{name}={s.machine}({s.os})" for name, s in self.sessions.items()
        )
        return f"MultiSession({machines})"

    def __enter__(self) -> MultiSession:
        self.connect_all()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close_all()
