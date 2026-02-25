"""
CUP -- Computer Use Protocol.

Cross-platform accessibility tree capture in a unified format.

Quick start::

    import cup

    # Session is the primary API — snapshot + actions
    session = cup.Session()
    tree = session.snapshot(scope="overview")    # window list only
    tree = session.snapshot(scope="foreground")  # foreground tree + window header
    tree = session.snapshot(scope="desktop")     # desktop items
    result = session.action("e14", "click")
    tree = session.snapshot(scope="foreground")  # re-snapshot after action

    # Convenience functions (use a default session internally)
    text = cup.snapshot()                        # foreground compact text (the default)
    text = cup.snapshot("full")                  # all windows compact text
    raw = cup.snapshot_raw()                     # foreground as CUP envelope dict
    raw = cup.snapshot_raw("full")               # all windows as CUP envelope dict
    text = cup.overview()                        # lightweight window list
"""

from __future__ import annotations

from typing import Any, Literal

from cup._router import detect_platform, get_adapter
from cup.actions import ActionExecutor, ActionResult
from cup.format import (
    Detail,
    build_envelope,
    prune_tree,
    serialize_compact,
    serialize_overview,
)

Scope = Literal["overview", "foreground", "desktop", "full"]

__all__ = [
    "snapshot",
    "snapshot_raw",
    "overview",
    "Session",
    "Scope",
    "ActionResult",
    # Advanced / building blocks
    "get_adapter",
    "detect_platform",
    "build_envelope",
    "serialize_compact",
    "serialize_overview",
    "prune_tree",
]


# ---------------------------------------------------------------------------
# Default session — used by the convenience functions below
# ---------------------------------------------------------------------------

_default_session: Session | None = None


def _get_default_session() -> Session:
    global _default_session
    if _default_session is None:
        _default_session = Session()
    return _default_session


# ---------------------------------------------------------------------------
# Convenience functions (thin wrappers around Session)
# ---------------------------------------------------------------------------


def snapshot(scope: Scope = "foreground", *, max_depth: int = 999) -> str:
    """Capture the screen as LLM-optimized compact text.

    Args:
        scope: What to capture — "foreground" (default), "full", "desktop", or "overview".
        max_depth: Maximum tree depth.
    """
    return _get_default_session().snapshot(
        scope=scope,
        max_depth=max_depth,
        compact=True,
    )


def snapshot_raw(scope: Scope = "foreground", *, max_depth: int = 999) -> dict:
    """Capture the screen as a structured CUP envelope dict.

    Args:
        scope: What to capture — "foreground" (default), "full", "desktop", or "overview".
        max_depth: Maximum tree depth.
    """
    return _get_default_session().snapshot(
        scope=scope,
        max_depth=max_depth,
        compact=False,
    )


def overview() -> str:
    """List all open windows (no tree walking). Near-instant."""
    return _get_default_session().snapshot(scope="overview", compact=True)


# ---------------------------------------------------------------------------
# Session — stateful tree capture with action execution
# ---------------------------------------------------------------------------


class Session:
    """A CUP session that captures trees with element references for action execution.

    Element IDs (e.g., "e14") are ephemeral — they are only valid for the
    most recent tree capture.  After executing any action, re-capture the
    tree to get fresh IDs.

    Example::

        session = cup.Session()
        overview = session.snapshot(scope="overview")    # what's running?
        tree = session.snapshot(scope="foreground")      # interact with app
        result = session.action("e7", "click")
        tree = session.snapshot(scope="foreground")      # fresh IDs after action
    """

    def __init__(self, *, platform: str | None = None) -> None:
        self._adapter = get_adapter(platform)
        self._executor = ActionExecutor(self._adapter)
        self._last_tree: list[dict] | None = None
        self._last_raw_tree: list[dict] | None = None

    def snapshot(
        self,
        *,
        scope: Scope = "foreground",
        app: str | None = None,
        max_depth: int = 999,
        compact: bool = True,
        detail: Detail = "standard",
    ) -> str | dict:
        """Capture the accessibility tree.

        Args:
            scope: Capture scope:
                "overview"   — Window list only (no tree walking, near-instant)
                "foreground" — Foreground window tree + window list in header
                "desktop"    — Desktop surface tree only
                "full"       — All windows tree
            app: Filter windows by title (only for scope="full").
            max_depth: Maximum tree depth.
            compact: If True, return compact LLM text; if False, return
                     the full CUP envelope dict.
            detail: Pruning level ("standard", "minimal", or "full").

        Returns:
            Compact text string or CUP envelope dict.
        """
        sw, sh, scale = self._adapter.get_screen_info()

        # --- overview scope: no tree walking ---
        if scope == "overview":
            window_list = self._adapter.get_window_list()
            if compact:
                return serialize_overview(
                    window_list,
                    platform=self._adapter.platform_name,
                    screen_w=sw,
                    screen_h=sh,
                )
            return {
                "version": "0.1.0",
                "platform": self._adapter.platform_name,
                "screen": {"w": sw, "h": sh},
                "scope": "overview",
                "tree": [],
                "windows": window_list,
            }

        # --- scopes that require tree walking ---
        window_list = None

        if scope == "foreground":
            win = self._adapter.get_foreground_window()
            windows = [win]
            app_name = win["title"]
            app_pid = win["pid"]
            app_bundle_id = win.get("bundle_id")
            # Get window list for header awareness
            window_list = self._adapter.get_window_list()
        elif scope == "desktop":
            desktop_win = self._adapter.get_desktop_window()
            if desktop_win is None:
                # Fallback: return overview for platforms without desktop
                window_list = self._adapter.get_window_list()
                if compact:
                    return serialize_overview(
                        window_list,
                        platform=self._adapter.platform_name,
                        screen_w=sw,
                        screen_h=sh,
                    )
                return {
                    "version": "0.1.0",
                    "platform": self._adapter.platform_name,
                    "screen": {"w": sw, "h": sh},
                    "scope": "overview",
                    "tree": [],
                    "windows": window_list,
                }
            windows = [desktop_win]
            app_name = "Desktop"
            app_pid = desktop_win.get("pid")
            app_bundle_id = desktop_win.get("bundle_id")
        else:  # "full"
            windows = self._adapter.get_all_windows()
            if app:
                app_lower = app.lower()
                windows = [w for w in windows if app_lower in (w.get("title") or "").lower()]
            app_name = None
            app_pid = None
            app_bundle_id = None

        tree, stats, refs = self._adapter.capture_tree(
            windows,
            max_depth=max_depth,
        )
        self._executor.set_refs(refs)

        tools = None
        if hasattr(self._adapter, "get_last_tools"):
            tools = self._adapter.get_last_tools() or None

        envelope = build_envelope(
            tree,
            platform=self._adapter.platform_name,
            scope=scope,
            screen_w=sw,
            screen_h=sh,
            screen_scale=scale,
            app_name=app_name,
            app_pid=app_pid,
            app_bundle_id=app_bundle_id,
            tools=tools,
        )

        # Store raw tree for semantic search + pruned tree for compact output
        self._last_raw_tree = envelope["tree"]
        self._last_tree = prune_tree(envelope["tree"], detail=detail)

        if compact:
            return serialize_compact(
                envelope,
                window_list=window_list,
                detail=detail,
            )
        return envelope

    def action(
        self,
        element_id: str,
        action: str,
        **params: Any,
    ) -> ActionResult:
        """Perform an action on an element from the last snapshot.

        Args:
            element_id: Element ID from the tree (e.g., "e14").
            action: CUP canonical action (click, type, toggle, etc.).
            **params: Action parameters (value, direction, etc.).
        """
        return self._executor.action(element_id, action, params)

    def press(self, combo: str) -> ActionResult:
        """Send a keyboard shortcut to the focused window.

        Args:
            combo: Key combination (e.g., "ctrl+s", "enter", "alt+f4").
        """
        return self._executor.press(combo)

    def open_app(self, name: str) -> ActionResult:
        """Open an application by name.

        Fuzzy-matches against installed apps (e.g., "chrome" matches
        "Google Chrome", "code" matches "Visual Studio Code").
        Waits for the app window to appear before returning.

        Args:
            name: Application name (fuzzy matched).
        """
        return self._executor.open_app(name)

    # -- find ---------------------------------------------------------------

    def find(
        self,
        *,
        query: str | None = None,
        role: str | None = None,
        name: str | None = None,
        state: str | None = None,
        limit: int = 5,
    ) -> list[dict]:
        """Search the last captured tree for matching elements.

        Searches the full unpruned tree with semantic role matching,
        fuzzy name matching, and relevance ranking.

        Args:
            query: Freeform semantic query (e.g., "play button", "search input").
            role: Role filter — exact CUP role or synonym (e.g., "search bar").
            name: Name filter — fuzzy token matching.
            state: State filter — exact match (e.g., "focused", "disabled").
            limit: Maximum results to return (default 5).

        Returns:
            List of matching CUP node dicts (without children), ranked by relevance.
        """
        if self._last_raw_tree is None:
            self.snapshot(scope="foreground", compact=True)

        from cup.search import search_tree

        results = search_tree(
            self._last_raw_tree,
            query=query,
            role=role,
            name=name,
            state=state,
            limit=limit,
        )
        return [r.node for r in results]

    # -- batch --------------------------------------------------------------

    def batch(
        self,
        actions: list[dict[str, Any]],
    ) -> list[ActionResult]:
        """Execute a sequence of actions, stopping on first failure.

        Each action spec is a dict with either:
            {"element_id": "e14", "action": "click"}
            {"element_id": "e5", "action": "type", "value": "hello"}
            {"action": "press", "keys": "ctrl+s"}
            {"action": "wait", "ms": 500}

        Returns:
            List of ActionResults — one per executed action.
            If an action fails, the list stops at that failure.
        """
        import time

        results: list[ActionResult] = []
        for spec in actions:
            action = spec.get("action", "")

            if action == "wait":
                ms = max(50, min(int(spec.get("ms", 500)), 5000))
                time.sleep(ms / 1000)
                result = ActionResult(success=True, message=f"Waited {ms}ms")
            elif action == "press":
                keys = spec.get("keys", "")
                if not keys:
                    results.append(
                        ActionResult(
                            success=False,
                            message="",
                            error="press action requires 'keys' parameter",
                        )
                    )
                    break
                result = self.press(keys)
            else:
                element_id = spec.get("element_id", "")
                if not element_id:
                    results.append(
                        ActionResult(
                            success=False,
                            message="",
                            error=f"Element action '{action}' requires 'element_id' parameter",
                        )
                    )
                    break
                params = {k: v for k, v in spec.items() if k not in ("element_id", "action")}
                result = self.action(element_id, action, **params)

            results.append(result)
            if not result.success:
                break

        return results

    # -- screenshot --------------------------------------------------------

    def screenshot(
        self,
        *,
        region: dict[str, int] | None = None,
    ) -> bytes:
        """Capture a screenshot and return PNG bytes.

        On macOS, uses the ``screencapture`` system utility and checks
        Screen Recording permission upfront — raises RuntimeError with
        a clear message if the permission is missing.

        On other platforms, requires the ``mss`` package:
        ``pip install cup[screenshot]``

        Args:
            region: Optional capture region {"x", "y", "w", "h"} in pixels.
                    If None, captures the full primary monitor.

        Returns:
            PNG image bytes.

        Raises:
            RuntimeError: On macOS if Screen Recording permission is not
                granted (System Settings > Privacy & Security > Screen Recording).
            ImportError: On other platforms if ``mss`` is not installed.
        """
        import sys

        if sys.platform == "darwin":
            return self._screenshot_macos(region)

        return self._screenshot_mss(region)

    def _screenshot_macos(self, region: dict[str, int] | None) -> bytes:
        """macOS screenshot via the ``screencapture`` system utility.

        All macOS screenshot APIs (mss, Quartz CGWindowListCreateImage,
        and screencapture) return only the desktop wallpaper when the
        calling process lacks Screen Recording permission. We detect
        this upfront and raise a clear error instead of returning a
        useless desktop-only image.
        """
        self._check_macos_screen_recording_permission()

        import os
        import subprocess
        import tempfile

        fd, tmp_path = tempfile.mkstemp(suffix=".png")
        os.close(fd)

        try:
            cmd = ["screencapture", "-x"]  # -x = no sound

            if region is not None:
                cmd.extend(
                    [
                        "-R",
                        f"{region['x']},{region['y']},{region['w']},{region['h']}",
                    ]
                )

            cmd.append(tmp_path)

            result = subprocess.run(cmd, capture_output=True, timeout=10)
            if result.returncode != 0:
                stderr = result.stderr.decode(errors="replace").strip()
                raise RuntimeError(f"screencapture failed (exit {result.returncode}): {stderr}")

            with open(tmp_path, "rb") as f:
                data = f.read()

            if not data:
                raise RuntimeError("screencapture produced an empty file")

            return data
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    @staticmethod
    def _check_macos_screen_recording_permission() -> None:
        """Check if this process has Screen Recording permission.

        Without it, all screenshot APIs silently return only the desktop
        wallpaper with no application windows visible. We detect this by
        checking if CGWindowListCopyWindowInfo returns any window names —
        macOS strips them when the process lacks permission.

        If permission is missing, we call CGRequestScreenCaptureAccess()
        to trigger the system prompt and raise a clear error.
        """
        from Quartz import (
            CGWindowListCopyWindowInfo,
            kCGNullWindowID,
            kCGWindowListOptionOnScreenOnly,
        )

        windows = CGWindowListCopyWindowInfo(
            kCGWindowListOptionOnScreenOnly,
            kCGNullWindowID,
        )

        # If any window has a name, we have permission
        has_permission = any(w.get("kCGWindowName") for w in (windows or []))

        if not has_permission:
            # Trigger the macOS permission prompt
            try:
                from Quartz import CGRequestScreenCaptureAccess

                CGRequestScreenCaptureAccess()
            except ImportError:
                pass

            raise RuntimeError(
                "Screen Recording permission is required for screenshots. "
                "Grant it to this app in: System Settings > Privacy & Security "
                "> Screen Recording. You may need to restart the app after granting."
            )

    def _screenshot_mss(self, region: dict[str, int] | None) -> bytes:
        """Fallback screenshot via mss (Windows/Linux)."""
        try:
            import mss
            import mss.tools
        except ImportError:
            raise ImportError(
                "Screenshot support requires the 'mss' package. "
                "Install it with: pip install cup[screenshot]"
            ) from None

        with mss.mss() as sct:
            if region is not None:
                monitor = {
                    "left": region["x"],
                    "top": region["y"],
                    "width": region["w"],
                    "height": region["h"],
                }
            else:
                monitor = sct.monitors[1]  # primary monitor

            img = sct.grab(monitor)
            return mss.tools.to_png(img.rgb, img.size)
