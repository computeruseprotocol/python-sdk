"""Tests for the CUP pagination system.

Tests find_node_by_id, serialize_page, and the updated clipping hints.
"""

from __future__ import annotations

from cup.format import (
    build_envelope,
    find_node_by_id,
    prune_tree,
    serialize_compact,
    serialize_page,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_node(id: str, role: str, name: str = "", **kwargs) -> dict:
    node = {"id": id, "role": role, "name": name}
    node.update(kwargs)
    return node


def _make_scrollable_list(item_count: int, viewport_items: int) -> dict:
    """Create a scrollable list with item_count children, viewport fits viewport_items."""
    item_height = 30
    viewport_height = viewport_items * item_height

    children = []
    for i in range(item_count):
        children.append(
            _make_node(
                f"e{i + 1}",
                "listitem",
                f"Item {i + 1}",
                bounds={"x": 0, "y": i * item_height, "w": 200, "h": item_height},
            )
        )

    lst = _make_node(
        "e0",
        "list",
        "Items",
        bounds={"x": 0, "y": 0, "w": 200, "h": viewport_height},
        actions=["scroll"],
        children=children,
    )

    envelope = build_envelope(
        [lst], platform="windows", screen_w=1920, screen_h=1080
    )
    return envelope


# ---------------------------------------------------------------------------
# find_node_by_id
# ---------------------------------------------------------------------------


class TestFindNodeById:
    def test_finds_root_node(self):
        tree = [_make_node("e0", "button", "Test")]
        result = find_node_by_id(tree, "e0")
        assert result is not None
        assert result["id"] == "e0"

    def test_finds_nested_node(self):
        tree = [
            _make_node(
                "e0",
                "group",
                children=[
                    _make_node(
                        "e1",
                        "group",
                        children=[_make_node("e2", "button", "Deep")],
                    )
                ],
            )
        ]
        result = find_node_by_id(tree, "e2")
        assert result is not None
        assert result["name"] == "Deep"

    def test_returns_none_for_missing_id(self):
        tree = [_make_node("e0", "button")]
        assert find_node_by_id(tree, "e99") is None

    def test_returns_none_for_empty_tree(self):
        assert find_node_by_id([], "e0") is None


# ---------------------------------------------------------------------------
# serialize_page
# ---------------------------------------------------------------------------


class TestSerializePage:
    def test_header_with_pagination_context(self):
        container = _make_node("e0", "list", "Items")
        items = [
            _make_node("e3", "listitem", "Item 3"),
            _make_node("e4", "listitem", "Item 4"),
        ]
        result = serialize_page(container, items, offset=2, total=10)
        assert '# page e0 | items 3-4 of 10 | lst "Items"' in result

    def test_footer_remaining_items(self):
        container = _make_node("e0", "list", "Items")
        items = [_make_node("e3", "listitem", "Item 3")]
        result = serialize_page(container, items, offset=2, total=10)
        assert "# 7 more — page(element_id='e0', direction='down')" in result

    def test_footer_preceding_items(self):
        container = _make_node("e0", "list", "Items")
        items = [_make_node("e5", "listitem", "Item 5")]
        result = serialize_page(container, items, offset=5, total=10)
        assert "# 5 before — page(element_id='e0', direction='up')" in result

    def test_no_remaining_hint_at_end(self):
        container = _make_node("e0", "list", "Items")
        items = [_make_node("e9", "listitem", "Item 10")]
        result = serialize_page(container, items, offset=9, total=10)
        assert "direction='down'" not in result

    def test_no_preceding_hint_at_start(self):
        container = _make_node("e0", "list", "Items")
        items = [_make_node("e0", "listitem", "Item 1")]
        result = serialize_page(container, items, offset=0, total=10)
        assert "direction='up'" not in result

    def test_renders_items_in_compact_format(self):
        container = _make_node("e0", "list", "Items")
        items = [_make_node("e3", "listitem", "Item 3")]
        result = serialize_page(container, items, offset=2, total=10)
        assert '[e3] li "Item 3"' in result


# ---------------------------------------------------------------------------
# Updated clipping hints
# ---------------------------------------------------------------------------


class TestClippingHints:
    def test_emits_page_hint_instead_of_scroll(self):
        envelope = _make_scrollable_list(20, 5)
        output = serialize_compact(envelope, detail="compact")
        assert "page(element_id='e0'" in output
        assert "direction='" in output
        assert "scroll down to see" not in output


# ---------------------------------------------------------------------------
# pruneTree clipping + page integration
# ---------------------------------------------------------------------------


class TestPruneTreeClipping:
    def test_attaches_clipped_metadata(self):
        envelope = _make_scrollable_list(20, 5)
        pruned = prune_tree(
            envelope["tree"],
            detail="compact",
            screen=envelope.get("screen"),
        )
        lst = pruned[0]
        assert "_clipped" in lst
        assert lst["_clipped"]["below"] > 0
