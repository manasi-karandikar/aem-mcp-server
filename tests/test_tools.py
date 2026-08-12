"""Unit tests for the pure logic behind each tool.

These cover the paths that real WKND content cannot reach — the item limit,
the truncation marker, and the path guardrail. That gap is the reason the
logic lives in `_`-prefixed functions separate from the tool wrappers.
"""
import pytest

from server import (
    TRUNCATION_MARK,
    _clean,
    _collect_text,
    _fragment_fields,
    _validate_path,
)


# --- guardrail -------------------------------------------------------------

@pytest.mark.parametrize("path", [
    "/etc/passwd",
    "/home/users/admin",
    "/libs/granite/security",
    "content/wknd",                # not absolute
    "/content/../etc/passwd",      # traversal
    "/contentfoo",                 # prefix confusion, not under /content
])
def test_validate_path_rejects(path):
    assert _validate_path(path) is not None


@pytest.mark.parametrize("path", [
    "/content",
    "/content/wknd/us/en",
    "/content/dam/wknd-shared/en/magazine/skitouring/skitouring",
])
def test_validate_path_allows(path):
    assert _validate_path(path) is None


# --- truncation ------------------------------------------------------------

def test_clean_strips_html_and_collapses_whitespace():
    assert _clean("<p>hello</p>\n  <b>world</b>") == "hello world"


def test_clean_marks_truncation():
    out = _clean("x" * 500, limit=100)
    assert out.endswith(TRUNCATION_MARK)


def test_clean_leaves_short_values_unmarked():
    assert TRUNCATION_MARK not in _clean("short value", limit=100)


# --- item limit ------------------------------------------------------------

def test_collect_text_stops_at_item_limit_and_reports_it():
    node = {f"comp{i}": {"text": f"value {i}"} for i in range(100)}
    out = []
    hit_limit = _collect_text(node, out, max_items=10)
    assert hit_limit is True
    assert len(out) == 10


def test_collect_text_reports_no_limit_when_under_budget():
    node = {f"comp{i}": {"text": f"value {i}"} for i in range(5)}
    out = []
    assert _collect_text(node, out, max_items=10) is False
    assert len(out) == 5


# --- fragment field extraction ---------------------------------------------

def test_fragment_fields_skips_metadata_and_namespaced_keys():
    variation = {
        "jcr:primaryType": "nt:unstructured",
        "title@LastModified": "Thu May 26 2022",
        "cq:tags": ["wknd:activity/skiing"],
        "title": "Ski Touring",
        "author": "Sofia Sjöberg",
    }
    joined = "\n".join(_fragment_fields(variation))
    assert "title: Ski Touring" in joined
    assert "author: Sofia Sjöberg" in joined
    assert "@LastModified" not in joined
    assert "jcr:primaryType" not in joined
    assert "cq:tags" not in joined
