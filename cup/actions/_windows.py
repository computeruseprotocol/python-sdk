"""Windows action handler — UIA pattern-based action execution + SendInput keyboard."""

from __future__ import annotations

import base64
import csv
import ctypes
import ctypes.wintypes
import difflib
import glob
import io
import os
import re
import subprocess
import time
from typing import Any

from cup.actions._handler import ActionHandler
from cup.actions.executor import ActionResult

# ---------------------------------------------------------------------------
# UIA pattern IDs
# ---------------------------------------------------------------------------

UIA_InvokePatternId = 10000
UIA_ValuePatternId = 10002
UIA_ScrollPatternId = 10004
UIA_ExpandCollapsePatternId = 10005
UIA_SelectionItemPatternId = 10010
UIA_TogglePatternId = 10015
UIA_RangeValuePatternId = 10013

# ---------------------------------------------------------------------------
# UIA pattern interfaces — lazily imported after comtypes generates them
# ---------------------------------------------------------------------------

_IInvoke = None
_IToggle = None
_IValue = None
_IExpandCollapse = None
_ISelectionItem = None
_IScroll = None
_IRangeValue = None


def _ensure_pattern_interfaces():
    global _IInvoke, _IToggle, _IValue, _IExpandCollapse
    global _ISelectionItem, _IScroll, _IRangeValue
    if _IInvoke is not None:
        return
    from comtypes.gen.UIAutomationClient import (
        IUIAutomationExpandCollapsePattern,
        IUIAutomationInvokePattern,
        IUIAutomationRangeValuePattern,
        IUIAutomationScrollPattern,
        IUIAutomationSelectionItemPattern,
        IUIAutomationTogglePattern,
        IUIAutomationValuePattern,
    )

    _IInvoke = IUIAutomationInvokePattern
    _IToggle = IUIAutomationTogglePattern
    _IValue = IUIAutomationValuePattern
    _IExpandCollapse = IUIAutomationExpandCollapsePattern
    _ISelectionItem = IUIAutomationSelectionItemPattern
    _IScroll = IUIAutomationScrollPattern
    _IRangeValue = IUIAutomationRangeValuePattern


def _get_pattern(element, pattern_id, interface):
    """Get a UIA pattern from an element, returning None if unavailable."""
    import comtypes

    try:
        pat = element.GetCurrentPattern(pattern_id)
        if pat:
            return pat.QueryInterface(interface)
    except (comtypes.COMError, Exception):
        pass
    return None


# ---------------------------------------------------------------------------
# Win32 SendInput keyboard
# ---------------------------------------------------------------------------

INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_UNICODE = 0x0004

VK_MAP = {
    "enter": 0x0D,
    "return": 0x0D,
    "tab": 0x09,
    "escape": 0x1B,
    "esc": 0x1B,
    "backspace": 0x08,
    "delete": 0x2E,
    "space": 0x20,
    "up": 0x26,
    "down": 0x28,
    "left": 0x25,
    "right": 0x27,
    "home": 0x24,
    "end": 0x23,
    "pageup": 0x21,
    "pagedown": 0x22,
    "f1": 0x70,
    "f2": 0x71,
    "f3": 0x72,
    "f4": 0x73,
    "f5": 0x74,
    "f6": 0x75,
    "f7": 0x76,
    "f8": 0x77,
    "f9": 0x78,
    "f10": 0x79,
    "f11": 0x7A,
    "f12": 0x7B,
    "ctrl": 0xA2,
    "alt": 0xA4,
    "shift": 0xA0,
    "win": 0x5B,
    "meta": 0x5B,
}

_EXTENDED_VKS = {
    0x26,
    0x28,
    0x25,
    0x27,  # arrow keys
    0x24,
    0x23,
    0x21,
    0x22,  # home, end, pageup, pagedown
    0x2E,  # delete
    0x5B,
    0x5C,  # VK_LWIN, VK_RWIN
}

ULONG_PTR = ctypes.c_uint64


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", ctypes.c_long),
        ("dy", ctypes.c_long),
        ("mouseData", ctypes.wintypes.DWORD),
        ("dwFlags", ctypes.wintypes.DWORD),
        ("time", ctypes.wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", ctypes.wintypes.WORD),
        ("wScan", ctypes.wintypes.WORD),
        ("dwFlags", ctypes.wintypes.DWORD),
        ("time", ctypes.wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", ctypes.wintypes.DWORD),
        ("wParamL", ctypes.wintypes.WORD),
        ("wParamH", ctypes.wintypes.WORD),
    ]


class _INPUT_UNION(ctypes.Union):
    _fields_ = [
        ("mi", MOUSEINPUT),
        ("ki", KEYBDINPUT),
        ("hi", HARDWAREINPUT),
    ]


class INPUT(ctypes.Structure):
    _fields_ = [
        ("type", ctypes.wintypes.DWORD),
        ("_input", _INPUT_UNION),
    ]


def _make_key_input(vk: int, *, down: bool = True) -> INPUT:
    flags = 0 if down else KEYEVENTF_KEYUP
    if vk in _EXTENDED_VKS:
        flags |= KEYEVENTF_EXTENDEDKEY
    inp = INPUT()
    inp.type = INPUT_KEYBOARD
    inp._input.ki.wVk = vk
    inp._input.ki.dwFlags = flags
    return inp


def _send_key_combo(keys_string: str) -> None:
    """Parse 'ctrl+s', 'enter', etc. and send via SendInput."""
    from cup.actions._keys import parse_combo

    mod_names, key_names = parse_combo(keys_string)

    # Map modifier names to VK codes ("meta" → VK_LWIN via VK_MAP["win"])
    _MOD_TO_VK = {"ctrl": 0xA2, "alt": 0xA4, "shift": 0xA0, "meta": 0x5B}
    modifiers = [_MOD_TO_VK[m] for m in mod_names if m in _MOD_TO_VK]

    main_keys = []
    for k in key_names:
        if k in VK_MAP:
            main_keys.append(VK_MAP[k])
        elif len(k) == 1:
            main_keys.append(ord(k.upper()))

    # When "super"/"win"/"meta" is pressed alone (no other keys), it's a
    # modifier-only press.  Treat it as the main key so it actually fires.
    if modifiers and not main_keys:
        main_keys = modifiers
        modifiers = []

    inputs = []
    for mod in modifiers:
        inputs.append(_make_key_input(mod, down=True))
    for key in main_keys:
        inputs.append(_make_key_input(key, down=True))
    for key in reversed(main_keys):
        inputs.append(_make_key_input(key, down=False))
    for mod in reversed(modifiers):
        inputs.append(_make_key_input(mod, down=False))

    if not inputs:
        raise RuntimeError(f"Could not resolve any key codes from combo: {keys_string!r}")

    # Send modifier-down events first, pause briefly, then the rest.
    # This gives the OS time to register modifier state before the main key,
    # which is important for system-level hotkeys like Win+R.
    n_mods = len(modifiers)
    if n_mods > 0 and len(inputs) > n_mods:
        mod_arr = (INPUT * n_mods)(*inputs[:n_mods])
        ctypes.windll.user32.SendInput(n_mods, mod_arr, ctypes.sizeof(INPUT))
        time.sleep(0.02)
        rest = inputs[n_mods:]
        rest_arr = (INPUT * len(rest))(*rest)
        sent = ctypes.windll.user32.SendInput(len(rest), rest_arr, ctypes.sizeof(INPUT))
    else:
        arr = (INPUT * len(inputs))(*inputs)
        sent = ctypes.windll.user32.SendInput(len(inputs), arr, ctypes.sizeof(INPUT))

    if sent == 0:
        err = ctypes.get_last_error()
        raise RuntimeError(f"SendInput failed, sent 0/{len(inputs)} events (error={err})")


def _send_unicode_string(text: str) -> None:
    """Send a string using KEYEVENTF_UNICODE scan codes.

    Unlike _send_key_combo which maps characters to virtual key codes
    (breaking special characters like :, /, -, .), this sends each
    character as a Unicode scan code — preserving all characters exactly.

    Control characters (newlines, tabs) are sent as virtual-key presses
    (VK_RETURN, VK_TAB) because many Windows apps — including the modern
    Windows 11 Notepad — do not interpret these when delivered as Unicode
    scan codes via KEYEVENTF_UNICODE.

    Long strings are sent in chunks with brief pauses so the target app's
    message queue can keep up.
    """
    # Normalize newlines: \r\n → \n, then standalone \r → \n.
    # We'll emit VK_RETURN for every \n below.
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    # Map control characters to their virtual-key codes.
    _CONTROL_VK = {
        "\n": 0x0D,  # VK_RETURN
        "\t": 0x09,  # VK_TAB
    }

    inputs: list[INPUT] = []
    for char in text:
        vk = _CONTROL_VK.get(char)
        if vk is not None:
            # Send control character as a normal virtual-key press.
            inputs.append(_make_key_input(vk, down=True))
            inputs.append(_make_key_input(vk, down=False))
        else:
            code = ord(char)
            # Key down
            inp_down = INPUT()
            inp_down.type = INPUT_KEYBOARD
            inp_down._input.ki.wVk = 0
            inp_down._input.ki.wScan = code
            inp_down._input.ki.dwFlags = KEYEVENTF_UNICODE
            inputs.append(inp_down)
            # Key up
            inp_up = INPUT()
            inp_up.type = INPUT_KEYBOARD
            inp_up._input.ki.wVk = 0
            inp_up._input.ki.wScan = code
            inp_up._input.ki.dwFlags = KEYEVENTF_UNICODE | KEYEVENTF_KEYUP
            inputs.append(inp_up)

    # Send all events in a single atomic SendInput call.
    _flush_inputs(inputs)


def _flush_inputs(inputs: list[INPUT]) -> None:
    """Send a batch of INPUT events via SendInput with a brief trailing pause."""
    if not inputs:
        return
    arr = (INPUT * len(inputs))(*inputs)
    sent = ctypes.windll.user32.SendInput(len(inputs), arr, ctypes.sizeof(INPUT))
    if sent == 0:
        err = ctypes.get_last_error()
        raise RuntimeError(f"SendInput (unicode) failed, sent 0/{len(inputs)} events (error={err})")
    # Brief pause gives the target app time to process the events before
    # the next chunk arrives.
    time.sleep(0.01)


# ---------------------------------------------------------------------------
# Win32 SendInput mouse
# ---------------------------------------------------------------------------

INPUT_MOUSE = 0
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_ABSOLUTE = 0x8000


def _get_element_click_point(element) -> tuple[int, int]:
    """Get the center point of a UIA element in screen coordinates."""
    rect = element.CurrentBoundingRectangle
    cx = (rect.left + rect.right) // 2
    cy = (rect.top + rect.bottom) // 2
    return cx, cy


def _screen_to_absolute(x: int, y: int) -> tuple[int, int]:
    """Convert screen pixel coordinates to SendInput absolute coordinates.

    SendInput absolute coordinates are normalized to 0-65535 range.
    """
    sm_cxscreen = ctypes.windll.user32.GetSystemMetrics(0)
    sm_cyscreen = ctypes.windll.user32.GetSystemMetrics(1)
    abs_x = int(x * 65535 / sm_cxscreen)
    abs_y = int(y * 65535 / sm_cyscreen)
    return abs_x, abs_y


def _send_mouse_click(
    x: int,
    y: int,
    *,
    button: str = "left",
    count: int = 1,
) -> None:
    """Send mouse click(s) at screen coordinates via SendInput."""
    abs_x, abs_y = _screen_to_absolute(x, y)

    if button == "right":
        down_flag = MOUSEEVENTF_RIGHTDOWN
        up_flag = MOUSEEVENTF_RIGHTUP
    else:
        down_flag = MOUSEEVENTF_LEFTDOWN
        up_flag = MOUSEEVENTF_LEFTUP

    inputs = []

    # Move cursor to position
    move = INPUT()
    move.type = INPUT_MOUSE
    move._input.mi.dx = abs_x
    move._input.mi.dy = abs_y
    move._input.mi.dwFlags = MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE
    inputs.append(move)

    # Click(s)
    for _ in range(count):
        down = INPUT()
        down.type = INPUT_MOUSE
        down._input.mi.dx = abs_x
        down._input.mi.dy = abs_y
        down._input.mi.dwFlags = down_flag | MOUSEEVENTF_ABSOLUTE
        inputs.append(down)

        up = INPUT()
        up.type = INPUT_MOUSE
        up._input.mi.dx = abs_x
        up._input.mi.dy = abs_y
        up._input.mi.dwFlags = up_flag | MOUSEEVENTF_ABSOLUTE
        inputs.append(up)

    arr = (INPUT * len(inputs))(*inputs)
    sent = ctypes.windll.user32.SendInput(
        len(inputs),
        arr,
        ctypes.sizeof(INPUT),
    )
    if sent == 0:
        err = ctypes.get_last_error()
        raise RuntimeError(f"SendInput mouse failed, sent 0/{len(inputs)} events (error={err})")


# ---------------------------------------------------------------------------
# WindowsActionHandler
# ---------------------------------------------------------------------------


class WindowsActionHandler(ActionHandler):
    """Execute CUP actions on Windows via UIA patterns + SendInput."""

    def __init__(self):
        self._initialized = False

    def _init(self):
        if self._initialized:
            return
        _ensure_pattern_interfaces()
        self._initialized = True

    def action(
        self,
        native_ref: Any,
        action: str,
        params: dict[str, Any],
    ) -> ActionResult:
        self._init()
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
            return self._adjust_range(element, increment=True)
        elif action == "decrement":
            return self._adjust_range(element, increment=False)
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
                error=f"Action '{action}' not implemented for Windows",
            )

    def press(self, combo: str) -> ActionResult:
        _send_key_combo(combo)
        return ActionResult(success=True, message=f"Pressed {combo}")

    # -- individual actions ------------------------------------------------

    def _click(self, element) -> ActionResult:
        pat = _get_pattern(element, UIA_InvokePatternId, _IInvoke)
        if pat:
            pat.Invoke()
            return ActionResult(success=True, message="Clicked")
        # Fallback: focus + enter
        try:
            element.SetFocus()
            time.sleep(0.05)
            _send_key_combo("enter")
            return ActionResult(success=True, message="Clicked (focus+enter fallback)")
        except Exception:
            return ActionResult(
                success=False,
                message="",
                error="Element does not support click",
            )

    def _toggle(self, element) -> ActionResult:
        pat = _get_pattern(element, UIA_TogglePatternId, _IToggle)
        if pat:
            pat.Toggle()
            return ActionResult(success=True, message="Toggled")
        return ActionResult(
            success=False,
            message="",
            error="Element does not support toggle",
        )

    def _type(self, element, text: str) -> ActionResult:
        """Type text into an element.

        Prefers ValuePattern.SetValue (instant, lossless) when available.
        Falls back to Unicode SendInput for elements that don't expose it.
        """
        import comtypes

        # Fast path: use ValuePattern to set text directly (no keyboard sim).
        try:
            pat = _get_pattern(element, UIA_ValuePatternId, _IValue)
            if pat:
                element.SetFocus()
                time.sleep(0.05)
                pat.SetValue(text)
                return ActionResult(success=True, message=f"Typed: {text}")
        except (comtypes.COMError, Exception):
            pass  # fall through to SendInput

        # Fallback: keyboard simulation via Unicode SendInput.
        try:
            element.SetFocus()
            time.sleep(0.05)
            _send_key_combo("ctrl+a")
            time.sleep(0.05)
            _send_unicode_string(text)
            return ActionResult(success=True, message=f"Typed: {text}")
        except Exception as exc:
            return ActionResult(success=False, message="", error=f"Failed to type: {exc}")

    def _setvalue(self, element, text: str) -> ActionResult:
        """Set value programmatically via UIA ValuePattern."""
        import comtypes

        pat = _get_pattern(element, UIA_ValuePatternId, _IValue)
        if pat:
            try:
                pat.SetValue(text)
                return ActionResult(success=True, message=f"Set value to: {text}")
            except comtypes.COMError as exc:
                return ActionResult(
                    success=False,
                    message="",
                    error=f"ValuePattern.SetValue failed: {exc}",
                )
        return ActionResult(
            success=False,
            message="",
            error="Element does not support ValuePattern (setvalue)",
        )

    def _expand(self, element) -> ActionResult:
        pat = _get_pattern(element, UIA_ExpandCollapsePatternId, _IExpandCollapse)
        if pat:
            pat.Expand()
            return ActionResult(success=True, message="Expanded")
        return ActionResult(
            success=False,
            message="",
            error="Element does not support expand",
        )

    def _collapse(self, element) -> ActionResult:
        pat = _get_pattern(element, UIA_ExpandCollapsePatternId, _IExpandCollapse)
        if pat:
            pat.Collapse()
            return ActionResult(success=True, message="Collapsed")
        return ActionResult(
            success=False,
            message="",
            error="Element does not support collapse",
        )

    def _select(self, element) -> ActionResult:
        pat = _get_pattern(element, UIA_SelectionItemPatternId, _ISelectionItem)
        if pat:
            pat.Select()
            return ActionResult(success=True, message="Selected")
        # Fallback: click
        return self._click(element)

    def _scroll(self, element, direction: str) -> ActionResult:
        pat = _get_pattern(element, UIA_ScrollPatternId, _IScroll)
        if pat:
            # ScrollAmount: 0=LargeDec 1=SmallDec 2=NoAmount 3=SmallInc 4=LargeInc
            h, v = 2, 2
            if direction == "up":
                v = 1
            elif direction == "down":
                v = 3
            elif direction == "left":
                h = 1
            elif direction == "right":
                h = 3
            pat.Scroll(h, v)
            return ActionResult(success=True, message=f"Scrolled {direction}")
        return ActionResult(
            success=False,
            message="",
            error="Element does not support scroll",
        )

    def _adjust_range(self, element, *, increment: bool) -> ActionResult:
        pat = _get_pattern(element, UIA_RangeValuePatternId, _IRangeValue)
        if pat:
            current = pat.CurrentValue
            small_change = pat.CurrentSmallChange
            step = small_change if small_change > 0 else 1.0
            new_val = current + step if increment else current - step
            # Clamp to range
            min_val = pat.CurrentMinimum
            max_val = pat.CurrentMaximum
            new_val = max(min_val, min(max_val, new_val))
            pat.SetValue(new_val)
            verb = "Incremented" if increment else "Decremented"
            return ActionResult(success=True, message=f"{verb} to {new_val}")
        return ActionResult(
            success=False,
            message="",
            error="Element does not support range value",
        )

    def _rightclick(self, element) -> ActionResult:
        try:
            x, y = _get_element_click_point(element)
            _send_mouse_click(x, y, button="right")
            return ActionResult(success=True, message="Right-clicked")
        except Exception as exc:
            return ActionResult(
                success=False,
                message="",
                error=f"Failed to right-click: {exc}",
            )

    def _doubleclick(self, element) -> ActionResult:
        try:
            x, y = _get_element_click_point(element)
            _send_mouse_click(x, y, count=2)
            return ActionResult(success=True, message="Double-clicked")
        except Exception as exc:
            return ActionResult(
                success=False,
                message="",
                error=f"Failed to double-click: {exc}",
            )

    def _focus(self, element) -> ActionResult:
        try:
            element.SetFocus()
            return ActionResult(success=True, message="Focused")
        except Exception as exc:
            return ActionResult(success=False, message="", error=f"Failed to focus: {exc}")

    def _dismiss(self, element) -> ActionResult:
        # Try close via window pattern, fallback to Alt+F4/Escape
        try:
            element.SetFocus()
            time.sleep(0.05)
            _send_key_combo("escape")
            return ActionResult(success=True, message="Dismissed (Escape)")
        except Exception as exc:
            return ActionResult(success=False, message="", error=f"Failed to dismiss: {exc}")

    def _longpress(self, element) -> ActionResult:
        """Long press: mouse down, hold 800ms, mouse up."""
        try:
            x, y = _get_element_click_point(element)
            abs_x, abs_y = _screen_to_absolute(x, y)

            # Move cursor
            move = INPUT()
            move.type = INPUT_MOUSE
            move._input.mi.dx = abs_x
            move._input.mi.dy = abs_y
            move._input.mi.dwFlags = MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE
            arr = (INPUT * 1)(move)
            ctypes.windll.user32.SendInput(1, arr, ctypes.sizeof(INPUT))

            # Press
            down = INPUT()
            down.type = INPUT_MOUSE
            down._input.mi.dx = abs_x
            down._input.mi.dy = abs_y
            down._input.mi.dwFlags = MOUSEEVENTF_LEFTDOWN | MOUSEEVENTF_ABSOLUTE
            arr = (INPUT * 1)(down)
            ctypes.windll.user32.SendInput(1, arr, ctypes.sizeof(INPUT))

            # Hold
            time.sleep(0.8)

            # Release
            up = INPUT()
            up.type = INPUT_MOUSE
            up._input.mi.dx = abs_x
            up._input.mi.dy = abs_y
            up._input.mi.dwFlags = MOUSEEVENTF_LEFTUP | MOUSEEVENTF_ABSOLUTE
            arr = (INPUT * 1)(up)
            ctypes.windll.user32.SendInput(1, arr, ctypes.sizeof(INPUT))

            return ActionResult(success=True, message="Long-pressed")
        except Exception as exc:
            return ActionResult(
                success=False,
                message="",
                error=f"Failed to long-press: {exc}",
            )

    # -- open_app --------------------------------------------------------------

    def open_app(self, name: str) -> ActionResult:
        """Launch a Windows application by name with fuzzy matching."""
        if not name or not name.strip():
            return ActionResult(
                success=False,
                message="",
                error="App name must not be empty",
            )

        try:
            apps = self._get_start_apps()
            if not apps:
                return ActionResult(
                    success=False,
                    message="",
                    error="Could not discover installed applications",
                )

            # Try matching against display names first.
            match = _fuzzy_match(name, list(apps.keys()))

            # If no match on display names, try matching against AppIDs.
            # This handles localized Windows where display names are
            # translated (e.g. "Notatnik" for Notepad on Polish Windows)
            # but AppIDs still contain the English name.
            if match is None:
                appid_to_name: dict[str, str] = {}
                for display, appid in apps.items():
                    # Extract a readable name from the AppID.
                    # UWP: "Microsoft.WindowsNotepad_8wekyb3d8bbwe!App" -> "WindowsNotepad"
                    # Path: just use the display name (already tried above).
                    parts = appid.split(".")
                    if len(parts) >= 2:
                        # Take the component after "Microsoft." etc., strip the suffix
                        raw = parts[-1].split("_")[0].split("!")[0]
                        appid_to_name[raw.lower()] = display
                appid_match = _fuzzy_match(name, list(appid_to_name.keys()))
                if appid_match is not None:
                    match = appid_to_name[appid_match]

            if match is None:
                return ActionResult(
                    success=False,
                    message="",
                    error=f"No installed app matching '{name}' found",
                )

            app_name, appid = match, apps[match]
            display_name = app_name.title()

            pid = self._launch_by_appid(appid)

            # Wait for window to appear
            if self._wait_for_window(pid, app_name):
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

    def _get_start_apps(self) -> dict[str, str]:
        """Discover installed apps via Get-StartApps, fallback to .lnk scan."""
        apps = self._get_apps_via_powershell()
        if apps:
            return apps
        return self._get_apps_from_shortcuts()

    def _get_apps_via_powershell(self) -> dict[str, str]:
        """Run Get-StartApps and parse the CSV output."""
        command = "Get-StartApps | ConvertTo-Csv -NoTypeInformation"
        output, ok = _run_powershell(command)
        if not ok or not output.strip():
            return {}

        apps: dict[str, str] = {}
        try:
            reader = csv.DictReader(io.StringIO(output.strip()))
            for row in reader:
                row_name = row.get("Name", "").strip()
                row_appid = row.get("AppID", "").strip()
                if row_name and row_appid:
                    apps[row_name.lower()] = row_appid
        except Exception:
            return {}
        return apps

    def _get_apps_from_shortcuts(self) -> dict[str, str]:
        """Scan Start Menu folders for .lnk shortcuts."""
        apps: dict[str, str] = {}
        search_dirs = [
            os.path.join(
                os.environ.get("ProgramData", r"C:\ProgramData"),
                r"Microsoft\Windows\Start Menu\Programs",
            ),
            os.path.join(
                os.environ.get("APPDATA", ""),
                r"Microsoft\Windows\Start Menu\Programs",
            ),
        ]
        for search_dir in search_dirs:
            if not os.path.isdir(search_dir):
                continue
            for lnk_path in glob.glob(os.path.join(search_dir, "**", "*.lnk"), recursive=True):
                lnk_name = os.path.splitext(os.path.basename(lnk_path))[0].lower()
                if lnk_name not in apps:
                    apps[lnk_name] = lnk_path
        return apps

    def _launch_by_appid(self, appid: str) -> int:
        """Launch an app by its AppID and return the PID (0 if unknown)."""
        if os.path.exists(appid) or "\\" in appid:
            # Path-based app (.lnk shortcut or direct .exe)
            safe = _ps_quote(appid)
            command = f"Start-Process {safe} -PassThru | Select-Object -ExpandProperty Id"
            output, ok = _run_powershell(command)
            if ok and output.strip().isdigit():
                return int(output.strip())
            return 0
        else:
            # UWP / Modern app with AppID
            safe = _ps_quote(f"shell:AppsFolder\\{appid}")
            command = f"Start-Process {safe}"
            _run_powershell(command)
            return 0

    def _wait_for_window(
        self,
        pid: int,
        app_name: str,
        timeout: float = 8.0,
    ) -> bool:
        """Poll for a new window matching the launched app."""
        EnumWindows = ctypes.windll.user32.EnumWindows
        GetWindowTextW = ctypes.windll.user32.GetWindowTextW
        GetWindowTextLengthW = ctypes.windll.user32.GetWindowTextLengthW
        IsWindowVisible = ctypes.windll.user32.IsWindowVisible
        GetWindowThreadProcessId = ctypes.windll.user32.GetWindowThreadProcessId

        WNDENUMPROC = ctypes.WINFUNCTYPE(
            ctypes.wintypes.BOOL,
            ctypes.wintypes.HWND,
            ctypes.wintypes.LPARAM,
        )

        deadline = time.monotonic() + timeout
        # Build a regex from the app name for title matching
        safe_name = re.escape(app_name)
        pattern = re.compile(safe_name, re.IGNORECASE)

        while time.monotonic() < deadline:
            found = False

            def callback(hwnd, _lparam):
                nonlocal found
                if not IsWindowVisible(hwnd):
                    return True

                # Check PID match
                if pid > 0:
                    win_pid = ctypes.wintypes.DWORD()
                    GetWindowThreadProcessId(hwnd, ctypes.byref(win_pid))
                    if win_pid.value == pid:
                        found = True
                        return False  # stop enumeration

                # Check title match
                length = GetWindowTextLengthW(hwnd)
                if length > 0:
                    buf = ctypes.create_unicode_buffer(length + 1)
                    GetWindowTextW(hwnd, buf, length + 1)
                    if pattern.search(buf.value):
                        found = True
                        return False

                return True

            EnumWindows(WNDENUMPROC(callback), 0)
            if found:
                return True
            time.sleep(0.5)

        return False


# ---------------------------------------------------------------------------
# open_app helpers
# ---------------------------------------------------------------------------


def _run_powershell(command: str, timeout: int = 10) -> tuple[str, bool]:
    """Run a PowerShell command using base64-encoded input. Returns (output, success)."""
    # Prepend a UTF-8 output-encoding directive so the stdout bytes are
    # valid UTF-8 regardless of the system's default codepage (e.g. cp1250
    # on Polish Windows which cannot represent many app names).
    full_command = "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; " + command
    encoded = base64.b64encode(full_command.encode("utf-16le")).decode("ascii")
    try:
        result = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-OutputFormat",
                "Text",
                "-EncodedCommand",
                encoded,
            ],
            capture_output=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
        )
        return result.stdout or "", result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return "", False


def _ps_quote(value: str) -> str:
    """Quote a string for PowerShell (single-quote with escaping)."""
    escaped = value.replace("'", "''")
    return f"'{escaped}'"


def _fuzzy_match(
    query: str,
    candidates: list[str],
    cutoff: float = 0.6,
) -> str | None:
    """Find the best fuzzy match for query among candidates.

    Returns the best matching candidate name, or None if no match
    meets the cutoff threshold.
    """
    query_lower = query.lower().strip()

    # Exact match first
    if query_lower in candidates:
        return query_lower

    # Substring match (e.g., "chrome" in "google chrome")
    for c in candidates:
        if query_lower in c:
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
