"""Linux action handler — AT-SPI2 + XTest/xdotool action execution."""

from __future__ import annotations

import ctypes
import ctypes.util
import difflib
import os
import re
import subprocess
import time
from typing import Any

from cup.actions._handler import ActionHandler
from cup.actions._keys import parse_combo
from cup.actions.executor import ActionResult

# ---------------------------------------------------------------------------
# X11 keysym mapping (XK_* constants)
# ---------------------------------------------------------------------------

_XK_MAP: dict[str, int] = {
    "enter": 0xFF0D,
    "return": 0xFF0D,
    "tab": 0xFF09,
    "escape": 0xFF1B,
    "space": 0x0020,
    "backspace": 0xFF08,
    "delete": 0xFFFF,
    "up": 0xFF52,
    "down": 0xFF54,
    "left": 0xFF51,
    "right": 0xFF53,
    "home": 0xFF50,
    "end": 0xFF57,
    "pageup": 0xFF55,
    "pagedown": 0xFF56,
    "insert": 0xFF63,
    "f1": 0xFFBE,
    "f2": 0xFFBF,
    "f3": 0xFFC0,
    "f4": 0xFFC1,
    "f5": 0xFFC2,
    "f6": 0xFFC3,
    "f7": 0xFFC4,
    "f8": 0xFFC5,
    "f9": 0xFFC6,
    "f10": 0xFFC7,
    "f11": 0xFFC8,
    "f12": 0xFFC9,
}

_XK_MODIFIERS: dict[str, int] = {
    "ctrl": 0xFFE3,   # XK_Control_L
    "alt": 0xFFE9,    # XK_Alt_L
    "shift": 0xFFE1,  # XK_Shift_L
    "meta": 0xFFEB,   # XK_Super_L
}


# ---------------------------------------------------------------------------
# XTest keyboard/mouse input via ctypes
# ---------------------------------------------------------------------------

class _XTest:
    """Thin ctypes wrapper around Xlib + XTest for input simulation."""

    def __init__(self):
        self._xlib = None
        self._xtst = None
        self._display = None

    def _ensure_open(self):
        if self._xlib is not None:
            return

        libx11_name = ctypes.util.find_library("X11")
        if not libx11_name:
            raise RuntimeError("libX11 not found. Install libx11-dev or xorg-x11-libs.")
        self._xlib = ctypes.cdll.LoadLibrary(libx11_name)

        libxtst_name = ctypes.util.find_library("Xtst")
        if not libxtst_name:
            raise RuntimeError(
                "libXtst not found. Install libxtst-dev or xorg-x11-server-utils."
            )
        self._xtst = ctypes.cdll.LoadLibrary(libxtst_name)

        display_name = os.environ.get("DISPLAY", ":0").encode()
        self._display = self._xlib.XOpenDisplay(display_name)
        if not self._display:
            raise RuntimeError(
                f"Cannot open X11 display '{display_name.decode()}'. "
                "Ensure DISPLAY is set and X server is running."
            )

        # Set up function signatures
        self._xlib.XKeysymToKeycode.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        self._xlib.XKeysymToKeycode.restype = ctypes.c_ubyte

        self._xtst.XTestFakeKeyEvent.argtypes = [
            ctypes.c_void_p, ctypes.c_uint, ctypes.c_int, ctypes.c_ulong,
        ]
        self._xtst.XTestFakeKeyEvent.restype = ctypes.c_int

        self._xtst.XTestFakeButtonEvent.argtypes = [
            ctypes.c_void_p, ctypes.c_uint, ctypes.c_int, ctypes.c_ulong,
        ]
        self._xtst.XTestFakeButtonEvent.restype = ctypes.c_int

        self._xtst.XTestFakeMotionEvent.argtypes = [
            ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_ulong,
        ]
        self._xtst.XTestFakeMotionEvent.restype = ctypes.c_int

    def keysym_to_keycode(self, keysym: int) -> int:
        self._ensure_open()
        return self._xlib.XKeysymToKeycode(self._display, keysym)

    def fake_key_event(self, keycode: int, is_press: bool, delay: int = 0):
        self._ensure_open()
        self._xtst.XTestFakeKeyEvent(self._display, keycode, int(is_press), delay)
        self._xlib.XFlush(self._display)

    def fake_button_event(self, button: int, is_press: bool, delay: int = 0):
        self._ensure_open()
        self._xtst.XTestFakeButtonEvent(self._display, button, int(is_press), delay)
        self._xlib.XFlush(self._display)

    def fake_motion_event(self, x: int, y: int, delay: int = 0):
        self._ensure_open()
        # screen_number = -1 means current screen
        self._xtst.XTestFakeMotionEvent(self._display, -1, x, y, delay)
        self._xlib.XFlush(self._display)

    def flush(self):
        if self._xlib and self._display:
            self._xlib.XFlush(self._display)


# Singleton instance — lazily initialized
_xtest: _XTest | None = None


def _get_xtest() -> _XTest:
    global _xtest
    if _xtest is None:
        _xtest = _XTest()
    return _xtest


# ---------------------------------------------------------------------------
# Input simulation helpers
# ---------------------------------------------------------------------------

def _send_key_combo(combo_str: str) -> None:
    """Send a keyboard combination via XTest fake key events."""
    xt = _get_xtest()
    mod_names, key_names = parse_combo(combo_str)

    # Resolve modifier keycodes
    mod_keycodes: list[int] = []
    for m in mod_names:
        keysym = _XK_MODIFIERS.get(m)
        if keysym:
            kc = xt.keysym_to_keycode(keysym)
            if kc:
                mod_keycodes.append(kc)

    # Resolve main keycodes
    main_keycodes: list[int] = []
    for k in key_names:
        if k in _XK_MAP:
            kc = xt.keysym_to_keycode(_XK_MAP[k])
            if kc:
                main_keycodes.append(kc)
        elif len(k) == 1:
            # Single character — use its Unicode codepoint as keysym
            # For ASCII, keysym == codepoint
            kc = xt.keysym_to_keycode(ord(k))
            if kc:
                main_keycodes.append(kc)

    # If only modifiers specified, treat them as main keys
    if mod_keycodes and not main_keycodes:
        main_keycodes = mod_keycodes
        mod_keycodes = []

    if not main_keycodes:
        raise RuntimeError(f"Could not resolve any key codes from combo: {combo_str!r}")

    # Press modifiers
    for kc in mod_keycodes:
        xt.fake_key_event(kc, True)
    time.sleep(0.01)

    # Press and release main keys
    for kc in main_keycodes:
        xt.fake_key_event(kc, True)
    time.sleep(0.01)
    for kc in reversed(main_keycodes):
        xt.fake_key_event(kc, False)

    # Release modifiers
    for kc in reversed(mod_keycodes):
        xt.fake_key_event(kc, False)

    xt.flush()
    time.sleep(0.01)


def _type_string(text: str) -> None:
    """Type a string using xdotool for reliable Unicode input.

    Falls back to XTest fake key events for ASCII-only text.
    """
    # For Unicode text, xdotool type is most reliable
    try:
        subprocess.run(
            ["xdotool", "type", "--clearmodifiers", "--", text],
            check=True,
            capture_output=True,
            timeout=10,
        )
        return
    except (FileNotFoundError, subprocess.SubprocessError):
        pass

    # Fallback: XTest for ASCII characters only
    xt = _get_xtest()
    for char in text:
        keysym = ord(char)
        kc = xt.keysym_to_keycode(keysym)
        if kc:
            xt.fake_key_event(kc, True)
            xt.fake_key_event(kc, False)
    xt.flush()
    time.sleep(0.01)


def _send_mouse_click(
    x: int,
    y: int,
    *,
    button: str = "left",
    count: int = 1,
) -> None:
    """Send mouse click(s) at screen coordinates via XTest."""
    xt = _get_xtest()

    # Button mapping: left=1, middle=2, right=3
    btn_num = 3 if button == "right" else 1

    # Move to position
    xt.fake_motion_event(x, y)
    time.sleep(0.02)

    # Click(s)
    for _ in range(count):
        xt.fake_button_event(btn_num, True)
        time.sleep(0.01)
        xt.fake_button_event(btn_num, False)
        time.sleep(0.02)

    xt.flush()
    time.sleep(0.01)


def _send_mouse_long_press(x: int, y: int, duration: float = 0.8) -> None:
    """Send a long press (mouse down, hold, mouse up) at screen coordinates."""
    xt = _get_xtest()

    xt.fake_motion_event(x, y)
    time.sleep(0.02)

    xt.fake_button_event(1, True)  # Left button down
    xt.flush()
    time.sleep(duration)

    xt.fake_button_event(1, False)  # Left button up
    xt.flush()
    time.sleep(0.01)


def _send_scroll(x: int, y: int, direction: str, amount: int = 5) -> None:
    """Send scroll events at screen coordinates via XTest.

    X11 scroll uses buttons 4 (up), 5 (down), 6 (left), 7 (right).
    """
    xt = _get_xtest()

    xt.fake_motion_event(x, y)
    time.sleep(0.02)

    button_map = {"up": 4, "down": 5, "left": 6, "right": 7}
    btn = button_map.get(direction, 5)

    for _ in range(amount):
        xt.fake_button_event(btn, True)
        xt.fake_button_event(btn, False)
        time.sleep(0.01)

    xt.flush()
    time.sleep(0.02)


# ---------------------------------------------------------------------------
# AT-SPI2 action helpers
# ---------------------------------------------------------------------------

def _atspi_do_action(accessible, action_name: str) -> bool:
    """Invoke a named action on an AT-SPI2 accessible object.

    Searches the Action interface for the matching action name
    and triggers it by index.
    """
    try:
        action_iface = accessible.get_action_iface()
        if action_iface is None:
            return False
        n = action_iface.get_n_actions()
        for i in range(n):
            name = (action_iface.get_action_name(i) or "").lower()
            if name == action_name:
                return action_iface.do_action(i)
        return False
    except Exception:
        return False


def _atspi_get_bounds_xywh(accessible) -> tuple[int, int, int, int] | None:
    """Get bounding rectangle (x, y, w, h) in screen coordinates."""
    try:
        comp = accessible.get_component_iface()
        if comp is None:
            return None
        # ATSPI_COORD_TYPE_SCREEN = 0
        rect = comp.get_extents(0)
        if rect.width > 0 or rect.height > 0:
            return (rect.x, rect.y, rect.width, rect.height)
    except Exception:
        pass
    return None


def _get_element_center(accessible) -> tuple[int, int] | None:
    """Get the center point of an AT-SPI2 element in screen coordinates."""
    bounds = _atspi_get_bounds_xywh(accessible)
    if bounds is None:
        return None
    x, y, w, h = bounds
    return x + w // 2, y + h // 2


def _atspi_grab_focus(accessible) -> bool:
    """Move keyboard focus to an element via the Component interface."""
    try:
        comp = accessible.get_component_iface()
        if comp is not None:
            return comp.grab_focus()
    except Exception:
        pass
    return False


def _atspi_get_value_iface(accessible):
    """Get the Value interface from an accessible, or None."""
    try:
        return accessible.get_value_iface()
    except Exception:
        return None


def _atspi_get_text_iface(accessible):
    """Get the Text interface from an accessible, or None."""
    try:
        return accessible.get_text_iface()
    except Exception:
        return None


def _atspi_get_editable_text_iface(accessible):
    """Get the EditableText interface from an accessible, or None."""
    try:
        return accessible.get_editable_text_iface()
    except Exception:
        return None


def _atspi_get_selection_iface(accessible):
    """Get the Selection interface from an accessible (usually the parent), or None."""
    try:
        return accessible.get_selection_iface()
    except Exception:
        return None


# ---------------------------------------------------------------------------
# App launching helpers
# ---------------------------------------------------------------------------

def _discover_desktop_apps() -> dict[str, str]:
    """Discover installed Linux apps from .desktop files.

    Returns {lowercase_name: desktop_file_path_or_exec_command}.
    """
    apps: dict[str, str] = {}

    # Standard XDG data directories
    xdg_data_dirs = os.environ.get(
        "XDG_DATA_DIRS", "/usr/local/share:/usr/share"
    ).split(":")
    xdg_data_home = os.environ.get(
        "XDG_DATA_HOME", os.path.expanduser("~/.local/share")
    )
    search_dirs = [xdg_data_home] + xdg_data_dirs

    for data_dir in search_dirs:
        app_dir = os.path.join(data_dir, "applications")
        if not os.path.isdir(app_dir):
            continue
        try:
            for root, _dirs, files in os.walk(app_dir):
                for fname in files:
                    if not fname.endswith(".desktop"):
                        continue
                    fpath = os.path.join(root, fname)
                    name, exec_cmd = _parse_desktop_file(fpath)
                    if name and exec_cmd:
                        key = name.lower()
                        if key not in apps:
                            apps[key] = exec_cmd
        except OSError:
            continue

    return apps


def _parse_desktop_file(path: str) -> tuple[str, str]:
    """Parse a .desktop file and return (Name, Exec) or ("", "")."""
    name = ""
    exec_cmd = ""
    no_display = False
    in_desktop_entry = False

    try:
        with open(path, encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if line == "[Desktop Entry]":
                    in_desktop_entry = True
                    continue
                if line.startswith("[") and line.endswith("]"):
                    if in_desktop_entry:
                        break  # End of [Desktop Entry] section
                    continue
                if not in_desktop_entry:
                    continue
                if line.startswith("Name=") and not name:
                    name = line[5:].strip()
                elif line.startswith("Exec="):
                    exec_cmd = line[5:].strip()
                    # Strip field codes (%f, %F, %u, %U, etc.)
                    exec_cmd = re.sub(r"\s+%[fFuUdDnNickvm]", "", exec_cmd).strip()
                elif line.startswith("NoDisplay=true"):
                    no_display = True
    except OSError:
        return "", ""

    if no_display:
        return "", ""
    return name, exec_cmd


def _fuzzy_match(
    query: str,
    candidates: list[str],
    cutoff: float = 0.5,
) -> str | None:
    """Find the best fuzzy match for query among candidates."""
    query_lower = query.lower().strip()

    # Exact match
    if query_lower in candidates:
        return query_lower

    # Substring match — prefer shorter candidates (more specific)
    substring_matches = [c for c in candidates if query_lower in c]
    if substring_matches:
        word_boundary = [
            c for c in substring_matches
            if re.search(r'(?:^|[\s\-_])' + re.escape(query_lower) + r'(?:$|[\s\-_])', c)
        ]
        if word_boundary:
            return min(word_boundary, key=len)
        return min(substring_matches, key=len)

    # Reverse substring
    for c in candidates:
        if c in query_lower:
            return c

    # Fuzzy match via SequenceMatcher
    best_match = None
    best_score = 0.0
    for c in candidates:
        score = difflib.SequenceMatcher(None, query_lower, c).ratio()
        if score > best_score:
            best_score = score
            best_match = c

    if best_match and best_score >= cutoff:
        return best_match
    return None


# ---------------------------------------------------------------------------
# LinuxActionHandler
# ---------------------------------------------------------------------------


class LinuxActionHandler(ActionHandler):
    """Execute CUP actions on Linux via AT-SPI2 + XTest/xdotool.

    Uses AT-SPI2 Action/Value/EditableText/Selection/Component interfaces
    for semantic actions, with XTest (libXtst) fallbacks for mouse/keyboard
    input simulation.

    Requirements:
      - libX11 and libXtst (for XTest fake events)
      - gi.repository.Atspi (PyGObject — usually already present for tree capture)
      - xdotool (optional, for reliable Unicode typing)
    """

    def action(
        self,
        native_ref: Any,
        action: str,
        params: dict[str, Any],
    ) -> ActionResult:
        element = native_ref

        if action == "click":
            return self._click(element)
        elif action == "toggle":
            return self._toggle(element)
        elif action == "type":
            value = params.get("value", "")
            return self._type(element, value)
        elif action == "setvalue":
            value = params.get("value", "")
            return self._setvalue(element, value)
        elif action == "expand":
            return self._expand(element)
        elif action == "collapse":
            return self._collapse(element)
        elif action == "select":
            return self._select(element)
        elif action == "scroll":
            direction = params.get("direction", "down")
            return self._scroll(element, direction)
        elif action == "increment":
            return self._increment(element)
        elif action == "decrement":
            return self._decrement(element)
        elif action == "rightclick":
            return self._rightclick(element)
        elif action == "doubleclick":
            return self._doubleclick(element)
        elif action == "focus":
            return self._focus(element)
        elif action == "dismiss":
            return self._dismiss(element)
        elif action == "longpress":
            return self._longpress(element)
        else:
            return ActionResult(
                success=False,
                message="",
                error=f"Action '{action}' not implemented for Linux",
            )

    def press(self, combo: str) -> ActionResult:
        try:
            _send_key_combo(combo)
            return ActionResult(success=True, message=f"Pressed {combo}")
        except Exception as exc:
            return ActionResult(
                success=False,
                message="",
                error=f"Failed to press keys '{combo}': {exc}",
            )

    # -- individual actions ------------------------------------------------

    def _click(self, element) -> ActionResult:
        # Try AT-SPI Action interface first (click, press, activate)
        for act_name in ("click", "press", "activate"):
            if _atspi_do_action(element, act_name):
                return ActionResult(success=True, message="Clicked")

        # Fallback: focus + Enter
        if _atspi_grab_focus(element):
            time.sleep(0.05)
            try:
                _send_key_combo("enter")
                return ActionResult(success=True, message="Clicked (focus+enter fallback)")
            except Exception:
                pass

        # Fallback: mouse click at element center
        center = _get_element_center(element)
        if center:
            try:
                _send_mouse_click(center[0], center[1])
                return ActionResult(success=True, message="Clicked (mouse fallback)")
            except Exception as exc:
                return ActionResult(
                    success=False, message="", error=f"Mouse click failed: {exc}"
                )

        return ActionResult(
            success=False,
            message="",
            error="Element does not support click and has no bounds",
        )

    def _toggle(self, element) -> ActionResult:
        # Try AT-SPI toggle action
        if _atspi_do_action(element, "toggle"):
            return ActionResult(success=True, message="Toggled")

        # Many checkboxes/switches use "click" to toggle
        if _atspi_do_action(element, "click"):
            return ActionResult(success=True, message="Toggled")

        return ActionResult(
            success=False, message="", error="Element does not support toggle"
        )

    def _type(self, element, text: str) -> ActionResult:
        """Type text into an element.

        Strategy:
        1. Try EditableText interface (insert/set text directly)
        2. Fall back to focus + XTest/xdotool keyboard input
        """
        try:
            # Strategy 1: EditableText interface (most reliable)
            editable = _atspi_get_editable_text_iface(element)
            if editable is not None:
                try:
                    # Select all existing text and replace
                    text_iface = _atspi_get_text_iface(element)
                    if text_iface:
                        char_count = text_iface.get_character_count()
                        if char_count > 0:
                            editable.delete_text(0, char_count)
                    editable.insert_text(0, text, len(text.encode("utf-8")))
                    return ActionResult(success=True, message=f"Typed: {text}")
                except Exception:
                    pass  # Fall through to keyboard input

            # Strategy 2: Focus + keyboard input
            _atspi_grab_focus(element)
            time.sleep(0.05)

            # Click to ensure focus
            center = _get_element_center(element)
            if center:
                _send_mouse_click(center[0], center[1])
                time.sleep(0.05)

            # Select all then type
            _send_key_combo("ctrl+a")
            time.sleep(0.05)
            _type_string(text)

            return ActionResult(success=True, message=f"Typed: {text}")
        except Exception as exc:
            return ActionResult(
                success=False, message="", error=f"Failed to type: {exc}"
            )

    def _setvalue(self, element, text: str) -> ActionResult:
        """Set value programmatically via AT-SPI2 Value or EditableText interface."""
        # Try Value interface (for sliders, spinbuttons)
        value_iface = _atspi_get_value_iface(element)
        if value_iface is not None:
            try:
                value_iface.set_current_value(float(text))
                return ActionResult(success=True, message=f"Set value to: {text}")
            except (ValueError, Exception):
                pass

        # Try EditableText interface
        editable = _atspi_get_editable_text_iface(element)
        if editable is not None:
            try:
                text_iface = _atspi_get_text_iface(element)
                if text_iface:
                    char_count = text_iface.get_character_count()
                    if char_count > 0:
                        editable.delete_text(0, char_count)
                editable.insert_text(0, text, len(text.encode("utf-8")))
                return ActionResult(success=True, message=f"Set value to: {text}")
            except Exception:
                pass

        # Fallback to type
        return self._type(element, text)

    def _expand(self, element) -> ActionResult:
        # Check if already expanded
        try:
            state_set = element.get_state_set()
            from gi.repository import Atspi
            if state_set.contains(Atspi.StateType.EXPANDED):
                return ActionResult(success=True, message="Already expanded")
        except Exception:
            pass

        # Try AT-SPI "expand or contract" action (GTK combo boxes)
        if _atspi_do_action(element, "expand or contract"):
            return ActionResult(success=True, message="Expanded")

        # Try click (works for disclosure triangles, tree items)
        if _atspi_do_action(element, "click") or _atspi_do_action(element, "activate"):
            return ActionResult(success=True, message="Expanded")

        return ActionResult(
            success=False, message="", error="Element does not support expand"
        )

    def _collapse(self, element) -> ActionResult:
        # Check if already collapsed
        try:
            state_set = element.get_state_set()
            from gi.repository import Atspi
            if not state_set.contains(Atspi.StateType.EXPANDED):
                return ActionResult(success=True, message="Already collapsed")
        except Exception:
            pass

        if _atspi_do_action(element, "expand or contract"):
            return ActionResult(success=True, message="Collapsed")

        if _atspi_do_action(element, "click") or _atspi_do_action(element, "activate"):
            return ActionResult(success=True, message="Collapsed")

        return ActionResult(
            success=False, message="", error="Element does not support collapse"
        )

    def _select(self, element) -> ActionResult:
        # Try Selection interface on the parent (e.g., list selects child)
        try:
            parent = element.get_parent()
            if parent:
                sel_iface = _atspi_get_selection_iface(parent)
                if sel_iface is not None:
                    # Find this element's index among siblings
                    idx = element.get_index_in_parent()
                    if idx >= 0 and sel_iface.select_child(idx):
                        return ActionResult(success=True, message="Selected")
        except Exception:
            pass

        # Try AT-SPI click action (works for tabs, menu items, list items)
        if _atspi_do_action(element, "click") or _atspi_do_action(element, "activate"):
            return ActionResult(success=True, message="Selected")

        # Fallback: mouse click
        return self._click(element)

    def _scroll(self, element, direction: str) -> ActionResult:
        center = _get_element_center(element)
        if center:
            try:
                _send_scroll(center[0], center[1], direction)
                return ActionResult(success=True, message=f"Scrolled {direction}")
            except Exception as exc:
                return ActionResult(
                    success=False, message="", error=f"Scroll failed: {exc}"
                )

        return ActionResult(
            success=False,
            message="",
            error="Element has no bounds for scroll target",
        )

    def _increment(self, element) -> ActionResult:
        # Try AT-SPI Action interface
        if _atspi_do_action(element, "increment"):
            return ActionResult(success=True, message="Incremented")

        # Try Value interface
        value_iface = _atspi_get_value_iface(element)
        if value_iface is not None:
            try:
                current = value_iface.get_current_value()
                minimum_increment = value_iface.get_minimum_increment()
                step = minimum_increment if minimum_increment > 0 else 1.0
                new_val = current + step
                maximum = value_iface.get_maximum_value()
                new_val = min(new_val, maximum)
                value_iface.set_current_value(new_val)
                return ActionResult(success=True, message=f"Incremented to {new_val}")
            except Exception:
                pass

        return ActionResult(
            success=False, message="", error="Element does not support increment"
        )

    def _decrement(self, element) -> ActionResult:
        if _atspi_do_action(element, "decrement"):
            return ActionResult(success=True, message="Decremented")

        value_iface = _atspi_get_value_iface(element)
        if value_iface is not None:
            try:
                current = value_iface.get_current_value()
                minimum_increment = value_iface.get_minimum_increment()
                step = minimum_increment if minimum_increment > 0 else 1.0
                new_val = current - step
                minimum = value_iface.get_minimum_value()
                new_val = max(new_val, minimum)
                value_iface.set_current_value(new_val)
                return ActionResult(success=True, message=f"Decremented to {new_val}")
            except Exception:
                pass

        return ActionResult(
            success=False, message="", error="Element does not support decrement"
        )

    def _rightclick(self, element) -> ActionResult:
        center = _get_element_center(element)
        if center:
            try:
                _send_mouse_click(center[0], center[1], button="right")
                return ActionResult(success=True, message="Right-clicked")
            except Exception as exc:
                return ActionResult(
                    success=False, message="", error=f"Right-click failed: {exc}"
                )

        return ActionResult(
            success=False,
            message="",
            error="Element has no bounds for right-click",
        )

    def _doubleclick(self, element) -> ActionResult:
        center = _get_element_center(element)
        if center:
            try:
                _send_mouse_click(center[0], center[1], count=2)
                return ActionResult(success=True, message="Double-clicked")
            except Exception as exc:
                return ActionResult(
                    success=False, message="", error=f"Double-click failed: {exc}"
                )

        return ActionResult(
            success=False,
            message="",
            error="Element has no bounds for double-click",
        )

    def _focus(self, element) -> ActionResult:
        if _atspi_grab_focus(element):
            return ActionResult(success=True, message="Focused")
        return ActionResult(
            success=False, message="", error="Failed to focus element"
        )

    def _dismiss(self, element) -> ActionResult:
        # Try AT-SPI close/dismiss action
        for act_name in ("close", "dismiss"):
            if _atspi_do_action(element, act_name):
                return ActionResult(success=True, message="Dismissed")

        # Fallback: focus + Escape
        try:
            _atspi_grab_focus(element)
            time.sleep(0.05)
            _send_key_combo("escape")
            return ActionResult(success=True, message="Dismissed (Escape)")
        except Exception as exc:
            return ActionResult(
                success=False, message="", error=f"Failed to dismiss: {exc}"
            )

    def _longpress(self, element) -> ActionResult:
        center = _get_element_center(element)
        if center:
            try:
                _send_mouse_long_press(center[0], center[1])
                return ActionResult(success=True, message="Long-pressed")
            except Exception as exc:
                return ActionResult(
                    success=False, message="", error=f"Long-press failed: {exc}"
                )

        return ActionResult(
            success=False,
            message="",
            error="Element has no bounds for long-press",
        )

    # -- open_app ----------------------------------------------------------

    def open_app(self, name: str) -> ActionResult:
        """Launch a Linux application by name with fuzzy matching.

        Discovers installed apps from .desktop files in XDG data directories,
        fuzzy-matches the name, and launches the best match.
        """
        if not name or not name.strip():
            return ActionResult(
                success=False, message="", error="App name must not be empty"
            )

        try:
            apps = _discover_desktop_apps()
            if not apps:
                return ActionResult(
                    success=False,
                    message="",
                    error="Could not discover installed applications",
                )

            match = _fuzzy_match(name, list(apps.keys()))
            if match is None:
                return ActionResult(
                    success=False,
                    message="",
                    error=f"No installed app matching '{name}' found",
                )

            exec_cmd = apps[match]
            display_name = match.title()

            # Launch via subprocess
            try:
                subprocess.Popen(
                    exec_cmd,
                    shell=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
            except Exception as exc:
                return ActionResult(
                    success=False,
                    message="",
                    error=f"Failed to launch '{display_name}': {exc}",
                )

            # Wait for window to appear
            if self._wait_for_window(match):
                return ActionResult(
                    success=True,
                    message=f"{display_name} launched",
                )
            return ActionResult(
                success=True,
                message=f"{display_name} launch sent, but window not yet detected",
            )

        except Exception as exc:
            return ActionResult(
                success=False,
                message="",
                error=f"Failed to launch '{name}': {exc}",
            )

    def _wait_for_window(
        self,
        app_name: str,
        timeout: float = 8.0,
    ) -> bool:
        """Poll AT-SPI2 desktop for a new window matching the launched app."""
        try:
            import gi
            gi.require_version("Atspi", "2.0")
            from gi.repository import Atspi
        except Exception:
            return False

        deadline = time.monotonic() + timeout
        pattern = re.compile(re.escape(app_name), re.IGNORECASE)

        while time.monotonic() < deadline:
            try:
                desktop = Atspi.get_desktop(0)
                for i in range(desktop.get_child_count()):
                    try:
                        app = desktop.get_child_at_index(i)
                        if app is None:
                            continue
                        name = (app.get_name() or "").lower()
                        if pattern.search(name):
                            # Check if app has at least one visible window
                            for j in range(app.get_child_count()):
                                try:
                                    win = app.get_child_at_index(j)
                                    if win is None:
                                        continue
                                    state_set = win.get_state_set()
                                    if state_set.contains(Atspi.StateType.VISIBLE):
                                        return True
                                except Exception:
                                    continue
                    except Exception:
                        continue
            except Exception:
                pass
            time.sleep(0.5)

        return False
