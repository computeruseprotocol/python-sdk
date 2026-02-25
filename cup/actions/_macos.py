"""macOS action handler — AXUIElement + Quartz CGEvent action execution."""

from __future__ import annotations

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
# Quartz CGEvent keyboard constants
# ---------------------------------------------------------------------------

# Virtual keycode mapping for macOS (CGKeyCode values)
_VK_MAP: dict[str, int] = {
    "enter": 0x24,
    "return": 0x24,
    "tab": 0x30,
    "escape": 0x35,
    "space": 0x31,
    "backspace": 0x33,
    "delete": 0x75,
    "up": 0x7E,
    "down": 0x7D,
    "left": 0x7B,
    "right": 0x7C,
    "home": 0x73,
    "end": 0x77,
    "pageup": 0x74,
    "pagedown": 0x79,
    "f1": 0x7A,
    "f2": 0x78,
    "f3": 0x63,
    "f4": 0x76,
    "f5": 0x60,
    "f6": 0x61,
    "f7": 0x62,
    "f8": 0x64,
    "f9": 0x65,
    "f10": 0x6D,
    "f11": 0x67,
    "f12": 0x6F,
    # Letters (lowercase)
    "a": 0x00,
    "b": 0x0B,
    "c": 0x08,
    "d": 0x02,
    "e": 0x0E,
    "f": 0x03,
    "g": 0x05,
    "h": 0x04,
    "i": 0x22,
    "j": 0x26,
    "k": 0x28,
    "l": 0x25,
    "m": 0x2E,
    "n": 0x2D,
    "o": 0x1F,
    "p": 0x23,
    "q": 0x0C,
    "r": 0x0F,
    "s": 0x01,
    "t": 0x11,
    "u": 0x20,
    "v": 0x09,
    "w": 0x0D,
    "x": 0x07,
    "y": 0x10,
    "z": 0x06,
    # Numbers
    "0": 0x1D,
    "1": 0x12,
    "2": 0x13,
    "3": 0x14,
    "4": 0x15,
    "5": 0x17,
    "6": 0x16,
    "7": 0x1A,
    "8": 0x1C,
    "9": 0x19,
    # Punctuation / symbols
    "-": 0x1B,
    "=": 0x18,
    "[": 0x21,
    "]": 0x1E,
    "\\": 0x2A,
    ";": 0x29,
    "'": 0x27,
    ",": 0x2B,
    ".": 0x2F,
    "/": 0x2C,
    "`": 0x32,
    "minus": 0x1B,
    "equal": 0x18,
    "plus": 0x18,
}

# Modifier flag bits for CGEventSetFlags
_kCGEventFlagMaskCommand = 1 << 20
_kCGEventFlagMaskShift = 1 << 17
_kCGEventFlagMaskAlternate = 1 << 19
_kCGEventFlagMaskControl = 1 << 18

_MOD_FLAGS: dict[str, int] = {
    "meta": _kCGEventFlagMaskCommand,
    "ctrl": _kCGEventFlagMaskControl,
    "alt": _kCGEventFlagMaskAlternate,
    "shift": _kCGEventFlagMaskShift,
}


def _send_key_combo(combo_str: str) -> None:
    """Send a keyboard combination via Quartz CGEvents."""
    from Quartz import (
        CGEventCreateKeyboardEvent,
        CGEventPost,
        CGEventSetFlags,
        kCGHIDEventTap,
    )

    mod_names, key_names = parse_combo(combo_str)

    # Build modifier flags mask
    flags = 0
    for m in mod_names:
        flags |= _MOD_FLAGS.get(m, 0)

    # Resolve main keycodes
    main_keys: list[int] = []
    for k in key_names:
        if k in _VK_MAP:
            main_keys.append(_VK_MAP[k])
        elif len(k) == 1 and k.lower() in _VK_MAP:
            main_keys.append(_VK_MAP[k.lower()])

    # If only modifiers were specified (e.g. "cmd"), treat them as key presses
    if not main_keys and mod_names:
        # Map modifier names to their virtual keycodes
        _MOD_VK: dict[str, int] = {
            "meta": 0x37,  # kVK_Command
            "ctrl": 0x3B,  # kVK_Control
            "alt": 0x3A,  # kVK_Option
            "shift": 0x38,  # kVK_Shift
        }
        for m in mod_names:
            if m in _MOD_VK:
                main_keys.append(_MOD_VK[m])
        flags = 0  # No modifier flags when pressing modifier alone

    if not main_keys:
        raise RuntimeError(f"Could not resolve any key codes from combo: {combo_str!r}")

    # Key down
    for vk in main_keys:
        event = CGEventCreateKeyboardEvent(None, vk, True)
        if flags:
            CGEventSetFlags(event, flags)
        CGEventPost(kCGHIDEventTap, event)

    time.sleep(0.01)

    # Key up
    for vk in reversed(main_keys):
        event = CGEventCreateKeyboardEvent(None, vk, False)
        if flags:
            CGEventSetFlags(event, flags)
        CGEventPost(kCGHIDEventTap, event)

    time.sleep(0.01)


def _type_string(text: str) -> None:
    """Type a string using CGEvents with Unicode support.

    Uses CGEventKeyboardSetUnicodeString for reliable Unicode input
    regardless of keyboard layout.
    """
    from Quartz import (
        CGEventCreateKeyboardEvent,
        CGEventKeyboardSetUnicodeString,
        CGEventPost,
        kCGHIDEventTap,
    )

    # Send in chunks — CGEventKeyboardSetUnicodeString supports up to 20 chars
    # per event reliably, but we'll do 1 char at a time for maximum compatibility
    for char in text:
        # Key down with Unicode char
        event_down = CGEventCreateKeyboardEvent(None, 0, True)
        CGEventKeyboardSetUnicodeString(event_down, len(char), char)
        CGEventPost(kCGHIDEventTap, event_down)

        # Key up
        event_up = CGEventCreateKeyboardEvent(None, 0, False)
        CGEventKeyboardSetUnicodeString(event_up, len(char), char)
        CGEventPost(kCGHIDEventTap, event_up)

    time.sleep(0.01)


# ---------------------------------------------------------------------------
# Quartz CGEvent mouse helpers
# ---------------------------------------------------------------------------


def _get_element_bounds(element) -> tuple[int, int, int, int] | None:
    """Get element bounds (x, y, w, h) from AXUIElement."""
    from ApplicationServices import (
        AXUIElementCopyAttributeValue,
        AXValueGetValue,
        kAXErrorSuccess,
        kAXPositionAttribute,
        kAXSizeAttribute,
        kAXValueCGPointType,
        kAXValueCGSizeType,
    )

    err, pos_ref = AXUIElementCopyAttributeValue(element, kAXPositionAttribute, None)
    if err != kAXErrorSuccess or pos_ref is None:
        return None

    err, size_ref = AXUIElementCopyAttributeValue(element, kAXSizeAttribute, None)
    if err != kAXErrorSuccess or size_ref is None:
        return None

    _, point = AXValueGetValue(pos_ref, kAXValueCGPointType, None)
    _, size = AXValueGetValue(size_ref, kAXValueCGSizeType, None)

    if point is None or size is None:
        return None

    return int(point.x), int(point.y), int(size.width), int(size.height)


def _get_element_center(element) -> tuple[float, float] | None:
    """Get center point of an element in screen coordinates."""
    bounds = _get_element_bounds(element)
    if bounds is None:
        return None
    x, y, w, h = bounds
    return x + w / 2.0, y + h / 2.0


def _get_element_center_or_parent(element) -> tuple[float, float] | None:
    """Get center point of an element, walking up parents if needed.

    Some elements (e.g., offscreen web content nodes in Safari) don't
    report valid bounds. This function walks up the AXParent chain to
    find the nearest ancestor with bounds, falling back to the window
    center as a last resort.
    """
    from ApplicationServices import AXUIElementCopyAttributeValue, kAXErrorSuccess

    current = element
    for _ in range(20):  # guard against infinite loops
        center = _get_element_center(current)
        if center is not None:
            return center
        # Walk up to parent
        err, parent = AXUIElementCopyAttributeValue(current, "AXParent", None)
        if err != kAXErrorSuccess or parent is None:
            break
        current = parent

    return None


def _send_mouse_click(
    x: float,
    y: float,
    *,
    button: str = "left",
    count: int = 1,
) -> None:
    """Send mouse click(s) at screen coordinates via Quartz CGEvents."""
    from Quartz import (
        CGEventCreateMouseEvent,
        CGEventPost,
        CGEventSetIntegerValueField,
        CGPointMake,
        kCGEventLeftMouseDown,
        kCGEventLeftMouseUp,
        kCGEventMouseMoved,
        kCGEventRightMouseDown,
        kCGEventRightMouseUp,
        kCGHIDEventTap,
        kCGMouseButtonLeft,
        kCGMouseButtonRight,
        kCGMouseEventClickState,
    )

    point = CGPointMake(x, y)

    if button == "right":
        down_type = kCGEventRightMouseDown
        up_type = kCGEventRightMouseUp
        mouse_button = kCGMouseButtonRight
    else:
        down_type = kCGEventLeftMouseDown
        up_type = kCGEventLeftMouseUp
        mouse_button = kCGMouseButtonLeft

    # Move cursor to position first
    move = CGEventCreateMouseEvent(None, kCGEventMouseMoved, point, kCGMouseButtonLeft)
    CGEventPost(kCGHIDEventTap, move)
    time.sleep(0.02)

    # Click(s)
    for i in range(count):
        click_number = i + 1
        down = CGEventCreateMouseEvent(None, down_type, point, mouse_button)
        CGEventSetIntegerValueField(down, kCGMouseEventClickState, click_number)
        CGEventPost(kCGHIDEventTap, down)

        time.sleep(0.01)

        up = CGEventCreateMouseEvent(None, up_type, point, mouse_button)
        CGEventSetIntegerValueField(up, kCGMouseEventClickState, click_number)
        CGEventPost(kCGHIDEventTap, up)

        if i < count - 1:
            time.sleep(0.02)

    time.sleep(0.01)


def _send_mouse_long_press(x: float, y: float, duration: float = 0.8) -> None:
    """Send a long press (mouse down, hold, mouse up) at screen coordinates."""
    from Quartz import (
        CGEventCreateMouseEvent,
        CGEventPost,
        CGPointMake,
        kCGEventLeftMouseDown,
        kCGEventLeftMouseUp,
        kCGEventMouseMoved,
        kCGHIDEventTap,
        kCGMouseButtonLeft,
    )

    point = CGPointMake(x, y)

    # Move cursor
    move = CGEventCreateMouseEvent(None, kCGEventMouseMoved, point, kCGMouseButtonLeft)
    CGEventPost(kCGHIDEventTap, move)
    time.sleep(0.02)

    # Press down
    down = CGEventCreateMouseEvent(None, kCGEventLeftMouseDown, point, kCGMouseButtonLeft)
    CGEventPost(kCGHIDEventTap, down)

    # Hold
    time.sleep(duration)

    # Release
    up = CGEventCreateMouseEvent(None, kCGEventLeftMouseUp, point, kCGMouseButtonLeft)
    CGEventPost(kCGHIDEventTap, up)
    time.sleep(0.01)


def _send_scroll(x: float, y: float, direction: str, amount: int = 5) -> None:
    """Send scroll event at screen coordinates via Quartz CGEvents.

    Uses pixel-based scrolling (kCGScrollEventUnitPixel) for reliable
    scrolling across all apps. Line-based scrolling (kCGScrollEventUnitLine)
    is unreliable in apps like Safari where line units may be interpreted
    as tiny or zero-pixel movements.
    """
    from Quartz import (
        CGEventCreateScrollWheelEvent,
        CGEventPost,
        CGEventSetLocation,
        CGPointMake,
        kCGHIDEventTap,
        kCGScrollEventUnitPixel,
    )

    point = CGPointMake(x, y)

    # Convert line amount to pixels (~80px per line is a reasonable default)
    pixel_amount = amount * 80

    if direction == "up":
        dy, dx = pixel_amount, 0
    elif direction == "down":
        dy, dx = -pixel_amount, 0
    elif direction == "left":
        dy, dx = 0, pixel_amount
    elif direction == "right":
        dy, dx = 0, -pixel_amount
    else:
        dy, dx = 0, 0

    event = CGEventCreateScrollWheelEvent(None, kCGScrollEventUnitPixel, 2, dy, dx)
    CGEventSetLocation(event, point)
    CGEventPost(kCGHIDEventTap, event)
    time.sleep(0.02)


# ---------------------------------------------------------------------------
# AXUIElement action helpers
# ---------------------------------------------------------------------------


def _ax_perform_action(element, action_name: str) -> bool:
    """Perform a named AX action on an element. Returns True on success."""
    from ApplicationServices import AXUIElementPerformAction, kAXErrorSuccess

    try:
        err = AXUIElementPerformAction(element, action_name)
        return err == kAXErrorSuccess
    except Exception:
        return False


def _ax_has_action(element, action_name: str) -> bool:
    """Check if an element supports a specific AX action."""
    from ApplicationServices import AXUIElementCopyActionNames, kAXErrorSuccess

    try:
        err, actions = AXUIElementCopyActionNames(element, None)
        if err == kAXErrorSuccess and actions:
            return action_name in actions
    except Exception:
        pass
    return False


def _ax_get_attr(element, attr: str, default=None):
    """Safely read a single AX attribute."""
    from ApplicationServices import AXUIElementCopyAttributeValue, kAXErrorSuccess

    try:
        err, value = AXUIElementCopyAttributeValue(element, attr, None)
        if err == kAXErrorSuccess and value is not None:
            return value
    except Exception:
        pass
    return default


def _ax_set_attr(element, attr: str, value) -> bool:
    """Set an AX attribute value. Returns True on success."""
    from ApplicationServices import AXUIElementSetAttributeValue, kAXErrorSuccess

    try:
        err = AXUIElementSetAttributeValue(element, attr, value)
        return err == kAXErrorSuccess
    except Exception:
        return False


def _ax_is_settable(element, attr: str) -> bool:
    """Check if an attribute is settable."""
    from ApplicationServices import AXUIElementIsAttributeSettable, kAXErrorSuccess

    try:
        err, settable = AXUIElementIsAttributeSettable(element, attr, None)
        if err == kAXErrorSuccess:
            return bool(settable)
    except Exception:
        pass
    return False


# ---------------------------------------------------------------------------
# App launching helpers
# ---------------------------------------------------------------------------


def _discover_apps() -> dict[str, str]:
    """Discover installed macOS apps. Returns {lowercase_name: path_or_bundle_id}."""
    apps: dict[str, str] = {}

    # Search common application directories
    app_dirs = [
        "/Applications",
        "/Applications/Utilities",
        "/System/Applications",
        "/System/Applications/Utilities",
        os.path.expanduser("~/Applications"),
    ]

    for app_dir in app_dirs:
        if not os.path.isdir(app_dir):
            continue
        try:
            for entry in os.listdir(app_dir):
                if entry.endswith(".app"):
                    app_name = entry[:-4]  # Remove .app
                    app_path = os.path.join(app_dir, entry)
                    apps[app_name.lower()] = app_path
        except OSError:
            continue

    # Also search via system_profiler for more apps (Homebrew casks, etc.)
    try:
        result = subprocess.run(
            ["mdfind", "kMDItemContentType == 'com.apple.application-bundle'"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            for line in result.stdout.strip().split("\n"):
                line = line.strip()
                if line.endswith(".app"):
                    app_name = os.path.basename(line)[:-4]
                    if app_name.lower() not in apps:
                        apps[app_name.lower()] = line
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass

    return apps


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
    # e.g. "code" should match "visual studio code" not "xcode"
    # and "chrome" should match "google chrome"
    substring_matches = [c for c in candidates if query_lower in c]
    if substring_matches:
        # Prefer candidates where query appears as a whole word boundary
        word_boundary = [
            c
            for c in substring_matches
            if re.search(r"(?:^|[\s\-_])" + re.escape(query_lower) + r"(?:$|[\s\-_])", c)
        ]
        if word_boundary:
            return min(word_boundary, key=len)
        return min(substring_matches, key=len)

    # Reverse substring (e.g. "google chrome" matches candidate "chrome")
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
# MacosActionHandler
# ---------------------------------------------------------------------------


class MacosActionHandler(ActionHandler):
    """Execute CUP actions on macOS via AXUIElement API + Quartz CGEvents."""

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
                error=f"Action '{action}' not implemented for macOS",
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
        # Try AXPress first (native accessibility action)
        if _ax_perform_action(element, "AXPress"):
            return ActionResult(success=True, message="Clicked")

        # Try AXConfirm
        if _ax_perform_action(element, "AXConfirm"):
            return ActionResult(success=True, message="Clicked (confirm)")

        # Fallback: mouse click at element center
        center = _get_element_center(element)
        if center:
            try:
                _send_mouse_click(center[0], center[1])
                return ActionResult(success=True, message="Clicked (mouse fallback)")
            except Exception as exc:
                return ActionResult(
                    success=False,
                    message="",
                    error=f"Mouse click failed: {exc}",
                )

        return ActionResult(
            success=False,
            message="",
            error="Element does not support click and has no bounds",
        )

    def _toggle(self, element) -> ActionResult:
        # AXPress toggles checkboxes/switches on macOS
        if _ax_perform_action(element, "AXPress"):
            return ActionResult(success=True, message="Toggled")
        return ActionResult(
            success=False,
            message="",
            error="Element does not support toggle",
        )

    def _type(self, element, text: str) -> ActionResult:
        """Type text into an element.

        Strategy:
        1. Try setting AXValue directly (most reliable, works for most text fields)
        2. Fall back to CGEvent keyboard typing (for elements that don't support AXValue)
        """
        try:
            # Focus the element first
            _ax_perform_action(element, "AXRaise")
            _ax_set_attr(element, "AXFocused", True)
            time.sleep(0.05)

            # Strategy 1: Set AXValue directly (preferred — bypasses keyboard entirely)
            if _ax_is_settable(element, "AXValue"):
                if _ax_set_attr(element, "AXValue", text):
                    return ActionResult(success=True, message=f"Typed: {text}")

            # Strategy 2: Click to ensure focus, select all, then type via CGEvent
            center = _get_element_center(element)
            if center:
                _send_mouse_click(center[0], center[1])
                time.sleep(0.05)

            _send_key_combo("meta+a")
            time.sleep(0.05)
            _type_string(text)
            return ActionResult(success=True, message=f"Typed: {text}")
        except Exception as exc:
            return ActionResult(
                success=False,
                message="",
                error=f"Failed to type: {exc}",
            )

    def _setvalue(self, element, text: str) -> ActionResult:
        """Set value programmatically via AXValue attribute."""
        if _ax_is_settable(element, "AXValue"):
            if _ax_set_attr(element, "AXValue", text):
                return ActionResult(success=True, message=f"Set value to: {text}")
            return ActionResult(
                success=False,
                message="",
                error="AXValue attribute set failed",
            )

        # Fallback: try typing
        return self._type(element, text)

    def _expand(self, element) -> ActionResult:
        # Check if already expanded
        expanded = _ax_get_attr(element, "AXExpanded")
        if expanded is not None and bool(expanded):
            return ActionResult(success=True, message="Already expanded")

        # Try AXPress (works for disclosure triangles, combo boxes)
        if _ax_perform_action(element, "AXPress"):
            return ActionResult(success=True, message="Expanded")

        # Try setting AXExpanded directly
        if _ax_set_attr(element, "AXExpanded", True):
            return ActionResult(success=True, message="Expanded")

        return ActionResult(
            success=False,
            message="",
            error="Element does not support expand",
        )

    def _collapse(self, element) -> ActionResult:
        # Check if already collapsed
        expanded = _ax_get_attr(element, "AXExpanded")
        if expanded is not None and not bool(expanded):
            return ActionResult(success=True, message="Already collapsed")

        # Try AXPress
        if _ax_perform_action(element, "AXPress"):
            return ActionResult(success=True, message="Collapsed")

        # Try setting AXExpanded directly
        if _ax_set_attr(element, "AXExpanded", False):
            return ActionResult(success=True, message="Collapsed")

        return ActionResult(
            success=False,
            message="",
            error="Element does not support collapse",
        )

    def _select(self, element) -> ActionResult:
        # Try AXPick (selection action)
        if _ax_perform_action(element, "AXPick"):
            return ActionResult(success=True, message="Selected")

        # Try AXPress (works for tabs, list items, menu items)
        if _ax_perform_action(element, "AXPress"):
            return ActionResult(success=True, message="Selected")

        # Try setting AXSelected
        if _ax_set_attr(element, "AXSelected", True):
            return ActionResult(success=True, message="Selected")

        # Fallback: click
        return self._click(element)

    def _scroll(self, element, direction: str) -> ActionResult:
        # Get element center for scroll target, walking up parents if needed.
        # Some elements (e.g., offscreen nodes in Safari) have no bounds,
        # so we fall back to the nearest ancestor with valid bounds.
        center = _get_element_center(element) or _get_element_center_or_parent(element)
        if center:
            try:
                _send_scroll(center[0], center[1], direction)
                return ActionResult(success=True, message=f"Scrolled {direction}")
            except Exception as exc:
                return ActionResult(
                    success=False,
                    message="",
                    error=f"Scroll failed: {exc}",
                )

        return ActionResult(
            success=False,
            message="",
            error="Element has no bounds for scroll target",
        )

    def _increment(self, element) -> ActionResult:
        if _ax_perform_action(element, "AXIncrement"):
            return ActionResult(success=True, message="Incremented")
        return ActionResult(
            success=False,
            message="",
            error="Element does not support increment",
        )

    def _decrement(self, element) -> ActionResult:
        if _ax_perform_action(element, "AXDecrement"):
            return ActionResult(success=True, message="Decremented")
        return ActionResult(
            success=False,
            message="",
            error="Element does not support decrement",
        )

    def _rightclick(self, element) -> ActionResult:
        # Try AXShowMenu (native context menu action)
        if _ax_perform_action(element, "AXShowMenu"):
            return ActionResult(success=True, message="Right-clicked (context menu)")

        # Fallback: mouse right-click at element center
        center = _get_element_center(element)
        if center:
            try:
                _send_mouse_click(center[0], center[1], button="right")
                return ActionResult(success=True, message="Right-clicked")
            except Exception as exc:
                return ActionResult(
                    success=False,
                    message="",
                    error=f"Right-click failed: {exc}",
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
                    success=False,
                    message="",
                    error=f"Double-click failed: {exc}",
                )

        return ActionResult(
            success=False,
            message="",
            error="Element has no bounds for double-click",
        )

    def _focus(self, element) -> ActionResult:
        # Try AXRaise first (brings window/element to front)
        _ax_perform_action(element, "AXRaise")

        # Set AXFocused
        if _ax_set_attr(element, "AXFocused", True):
            return ActionResult(success=True, message="Focused")

        # AXRaise succeeded even if AXFocused didn't apply
        if _ax_has_action(element, "AXRaise"):
            return ActionResult(success=True, message="Focused (raised)")

        return ActionResult(
            success=False,
            message="",
            error="Failed to focus element",
        )

    def _dismiss(self, element) -> ActionResult:
        # Try AXCancel (native dismiss action for dialogs/sheets)
        if _ax_perform_action(element, "AXCancel"):
            return ActionResult(success=True, message="Dismissed")

        # Fallback: send Escape key
        try:
            _ax_perform_action(element, "AXRaise")
            _ax_set_attr(element, "AXFocused", True)
            time.sleep(0.05)
            _send_key_combo("escape")
            return ActionResult(success=True, message="Dismissed (Escape)")
        except Exception as exc:
            return ActionResult(
                success=False,
                message="",
                error=f"Failed to dismiss: {exc}",
            )

    def _longpress(self, element) -> ActionResult:
        center = _get_element_center(element)
        if center:
            try:
                _send_mouse_long_press(center[0], center[1])
                return ActionResult(success=True, message="Long-pressed")
            except Exception as exc:
                return ActionResult(
                    success=False,
                    message="",
                    error=f"Long-press failed: {exc}",
                )

        return ActionResult(
            success=False,
            message="",
            error="Element has no bounds for long-press",
        )

    # -- open_app ----------------------------------------------------------

    def open_app(self, name: str) -> ActionResult:
        """Launch a macOS application by name with fuzzy matching."""
        if not name or not name.strip():
            return ActionResult(
                success=False,
                message="",
                error="App name must not be empty",
            )

        try:
            apps = _discover_apps()
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

            app_path = apps[match]
            display_name = match.title()

            # Launch via NSWorkspace (preferred) or open command
            launched = self._launch_via_nsworkspace(app_path)
            if not launched:
                launched = self._launch_via_open(app_path)

            if not launched:
                return ActionResult(
                    success=False,
                    message="",
                    error=f"Failed to launch '{display_name}'",
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

    def _launch_via_nsworkspace(self, app_path: str) -> bool:
        """Launch app via NSWorkspace."""
        try:
            from AppKit import NSWorkspace

            workspace = NSWorkspace.sharedWorkspace()

            if app_path.endswith(".app") and os.path.isdir(app_path):
                return bool(workspace.launchApplication_(app_path))

            # Try as bundle identifier
            return bool(
                workspace.launchAppWithBundleIdentifier_options_additionalEventParamDescriptor_launchIdentifier_(
                    app_path,
                    0,
                    None,
                    None,
                )
            )
        except Exception:
            return False

    def _launch_via_open(self, app_path: str) -> bool:
        """Launch app via `open` command (fallback)."""
        try:
            if app_path.endswith(".app") and os.path.isdir(app_path):
                result = subprocess.run(
                    ["open", "-a", app_path],
                    capture_output=True,
                    timeout=10,
                )
            else:
                result = subprocess.run(
                    ["open", "-b", app_path],
                    capture_output=True,
                    timeout=10,
                )
            return result.returncode == 0
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            return False

    def _wait_for_window(
        self,
        app_name: str,
        timeout: float = 8.0,
    ) -> bool:
        """Poll for a window matching the launched app.

        Uses both NSWorkspace (for activation policy filtering) and
        CGWindowListCopyWindowInfo (for fresh window-server data) to
        detect when the launched app's window appears.
        """
        from AppKit import NSApplicationActivationPolicyRegular, NSWorkspace

        deadline = time.monotonic() + timeout
        pattern = re.compile(re.escape(app_name), re.IGNORECASE)

        while time.monotonic() < deadline:
            # Strategy 1: NSWorkspace (may be stale in long-running processes)
            workspace = NSWorkspace.sharedWorkspace()
            for app in workspace.runningApplications():
                if app.activationPolicy() != NSApplicationActivationPolicyRegular:
                    continue
                name = app.localizedName() or ""
                if pattern.search(name.lower()):
                    pid = app.processIdentifier()
                    try:
                        from ApplicationServices import (
                            AXUIElementCopyAttributeValue,
                            AXUIElementCreateApplication,
                            kAXErrorSuccess,
                            kAXWindowsAttribute,
                        )

                        app_ref = AXUIElementCreateApplication(pid)
                        err, windows = AXUIElementCopyAttributeValue(
                            app_ref,
                            kAXWindowsAttribute,
                            None,
                        )
                        if err == kAXErrorSuccess and windows and len(windows) > 0:
                            return True
                    except Exception:
                        pass

            # Strategy 2: CGWindowListCopyWindowInfo (always fresh from window server)
            try:
                from Quartz import (
                    CGWindowListCopyWindowInfo,
                    kCGNullWindowID,
                    kCGWindowListOptionOnScreenOnly,
                )

                cg_windows = CGWindowListCopyWindowInfo(
                    kCGWindowListOptionOnScreenOnly,
                    kCGNullWindowID,
                )
                if cg_windows:
                    for w in cg_windows:
                        layer = w.get("kCGWindowLayer", -1)
                        if layer != 0:
                            continue
                        owner = w.get("kCGWindowOwnerName", "")
                        if owner and pattern.search(owner.lower()):
                            return True
            except Exception:
                pass

            time.sleep(0.5)

        return False
