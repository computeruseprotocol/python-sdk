"""Tests for batch functionality."""

from __future__ import annotations

from cup.actions.executor import ActionResult

# ---------------------------------------------------------------------------
# Batch logic (mirrors Session.batch without needing an adapter)
# ---------------------------------------------------------------------------


def _batch(actions, action_fn, press_fn):
    """Simulate Session.batch with injected action functions."""
    results = []
    for spec in actions:
        action = spec.get("action", "")
        if action == "press":
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
            result = press_fn(keys)
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
            result = action_fn(element_id, action, **params)
        results.append(result)
        if not result.success:
            break
    return results


_ok = lambda msg="OK": ActionResult(success=True, message=msg)
_fail = lambda err="Failed": ActionResult(success=False, message="", error=err)


class TestBatch:
    def test_all_succeed(self):
        results = _batch(
            [
                {"element_id": "e1", "action": "click"},
                {"action": "press", "keys": "enter"},
                {"element_id": "e2", "action": "type", "value": "hello"},
            ],
            action_fn=lambda eid, action, **p: _ok(f"{action} {eid}"),
            press_fn=lambda keys: _ok(f"Pressed {keys}"),
        )
        assert len(results) == 3
        assert all(r.success for r in results)

    def test_stops_on_first_failure(self):
        call_count = [0]

        def do_action(eid, action, **params):
            call_count[0] += 1
            if eid == "e2":
                return _fail("Not found")
            return _ok()

        results = _batch(
            [
                {"element_id": "e1", "action": "click"},
                {"element_id": "e2", "action": "click"},  # fails
                {"element_id": "e3", "action": "click"},  # never reached
            ],
            action_fn=do_action,
            press_fn=lambda keys: _ok(),
        )
        assert len(results) == 2
        assert results[0].success is True
        assert results[1].success is False
        assert call_count[0] == 2

    def test_empty_actions_list(self):
        results = _batch(
            [],
            action_fn=lambda *a, **k: _ok(),
            press_fn=lambda *a: _ok(),
        )
        assert results == []

    def test_press_without_keys_param(self):
        results = _batch(
            [{"action": "press"}],
            action_fn=lambda *a, **k: _ok(),
            press_fn=lambda *a: _ok(),
        )
        assert len(results) == 1
        assert results[0].success is False
        assert "keys" in results[0].error.lower()

    def test_element_action_without_element_id(self):
        results = _batch(
            [{"action": "click"}],
            action_fn=lambda *a, **k: _ok(),
            press_fn=lambda *a: _ok(),
        )
        assert len(results) == 1
        assert results[0].success is False
        assert "element_id" in results[0].error.lower()

    def test_params_forwarded(self):
        received = {}

        def do_action(eid, action, **params):
            received.update(params)
            return _ok()

        _batch(
            [{"element_id": "e1", "action": "type", "value": "hello"}],
            action_fn=do_action,
            press_fn=lambda *a: _ok(),
        )
        assert received == {"value": "hello"}

    def test_mixed_element_and_press(self):
        log = []

        def do_action(eid, action, **params):
            log.append(("action", eid, action))
            return _ok()

        def press(keys):
            log.append(("press", keys))
            return _ok()

        results = _batch(
            [
                {"element_id": "e1", "action": "click"},
                {"action": "press", "keys": "tab"},
                {"element_id": "e2", "action": "type", "value": "world"},
            ],
            action_fn=do_action,
            press_fn=press,
        )
        assert len(results) == 3
        assert log == [
            ("action", "e1", "click"),
            ("press", "tab"),
            ("action", "e2", "type"),
        ]
