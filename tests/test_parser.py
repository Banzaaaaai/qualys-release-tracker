"""
tests/test_parser.py — Qualys Release Tracker
==============================================
Offline unit tests for parse_releases().
Run with:  pytest tests/test_parser.py -v
"""

import hashlib
from textwrap import dedent

import pytest

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from scraper import parse_releases, _priority, HIGH_PRIORITY_TAGS, MEDIUM_PRIORITY_TAGS


# ── HTML fixtures ─────────────────────────────────────────────────────────────

MINIMAL_HTML = dedent("""\
    <html><body><ul>
      <li class="category-header">May 2025</li>
      <li class="releasenotes-item">
        <a href="https://qualys.com/notes/vmdr-2025-05-01">VMDR Update May 2025</a>
        <div title="VMDR">VMDR</div>
        <div title="PC">PC</div>
      </li>
      <li class="releasenotes-item">
        <a href="https://qualys.com/notes/edr-2025-04-20">EDR Patch April 2025</a>
        <div title="EDR">EDR</div>
      </li>
    </ul></body></html>
""")

MULTI_MONTH_HTML = dedent("""\
    <html><body><ul>
      <li class="category-header">June 2025</li>
      <li class="releasenotes-item">
        <a href="https://qualys.com/notes/api-june">API Release June</a>
        <div title="API">API</div>
      </li>
      <li class="category-header">May 2025</li>
      <li class="releasenotes-item">
        <a href="https://qualys.com/notes/fim-may">FIM Update May</a>
        <div title="FIM">FIM</div>
      </li>
      <li class="releasenotes-item">
        <a href="https://qualys.com/notes/other-may">Other Tool May</a>
      </li>
    </ul></body></html>
""")

NO_RELEASES_HTML = "<html><body><p>No release notes available.</p></body></html>"

MISSING_ANCHOR_HTML = dedent("""\
    <html><body><ul>
      <li class="releasenotes-item"><span>No anchor here</span></li>
      <li class="releasenotes-item">
        <a href="https://qualys.com/notes/valid">Valid Release</a>
        <div title="VM">VM</div>
      </li>
    </ul></body></html>
""")

DUPLICATE_URL_HTML = dedent("""\
    <html><body><ul>
      <li class="releasenotes-item">
        <a href="https://qualys.com/notes/same">Release A</a>
        <div title="VM">VM</div>
      </li>
      <li class="releasenotes-item">
        <a href="https://qualys.com/notes/same">Release A duplicate</a>
        <div title="VM">VM</div>
      </li>
    </ul></body></html>
""")


# ── Tests: parse_releases ─────────────────────────────────────────────────────

class TestParseReleases:

    def test_returns_list(self):
        assert isinstance(parse_releases(MINIMAL_HTML), list)

    def test_correct_count(self):
        assert len(parse_releases(MINIMAL_HTML)) == 2

    def test_required_fields_present(self):
        for entry in parse_releases(MINIMAL_HTML):
            for field in ("key", "url", "title", "tags", "month_label"):
                assert field in entry, f"Missing field '{field}'"

    def test_key_is_sha1_prefix_of_url(self):
        entry = parse_releases(MINIMAL_HTML)[0]
        assert entry["key"] == hashlib.sha1(entry["url"].encode()).hexdigest()[:12]

    def test_key_length_is_12(self):
        for entry in parse_releases(MINIMAL_HTML):
            assert len(entry["key"]) == 12

    def test_tags_extracted_correctly(self):
        result = parse_releases(MINIMAL_HTML)
        vmdr_entry = next(r for r in result if "VMDR" in r["title"])
        assert "VMDR" in vmdr_entry["tags"]
        assert "PC"   in vmdr_entry["tags"]

    def test_tags_empty_when_no_divs(self):
        result = parse_releases(MULTI_MONTH_HTML)
        no_tag = next(r for r in result if "other-may" in r["url"])
        assert no_tag["tags"] == []

    def test_month_label_single_month(self):
        for entry in parse_releases(MINIMAL_HTML):
            assert entry["month_label"] == "May 2025"

    def test_month_label_multi_month(self):
        result     = parse_releases(MULTI_MONTH_HTML)
        june_entry = next(r for r in result if "june" in r["url"])
        may_entry  = next(r for r in result if "fim-may" in r["url"])
        assert june_entry["month_label"] == "June 2025"
        assert may_entry["month_label"]  == "May 2025"

    def test_empty_page_returns_empty_list(self):
        assert parse_releases(NO_RELEASES_HTML) == []

    def test_item_without_anchor_is_skipped(self):
        result = parse_releases(MISSING_ANCHOR_HTML)
        assert len(result) == 1
        assert result[0]["url"] == "https://qualys.com/notes/valid"

    def test_duplicate_url_produces_same_key(self):
        result = parse_releases(DUPLICATE_URL_HTML)
        assert result[0]["key"] == result[1]["key"]

    def test_title_is_non_empty_string(self):
        for entry in parse_releases(MINIMAL_HTML):
            assert isinstance(entry["title"], str) and len(entry["title"]) > 0

    def test_url_starts_with_http(self):
        for entry in parse_releases(MINIMAL_HTML):
            assert entry["url"].startswith("http")


# ── Tests: _priority ──────────────────────────────────────────────────────────

class TestPriority:

    @pytest.mark.parametrize("tag", sorted(HIGH_PRIORITY_TAGS))
    def test_high_priority_tags(self, tag):
        label, colour = _priority([tag])
        assert "HIGH" in label
        assert colour == "#c0392b"

    @pytest.mark.parametrize("tag", sorted(MEDIUM_PRIORITY_TAGS))
    def test_medium_priority_tags(self, tag):
        label, colour = _priority([tag])
        assert "MEDIUM" in label
        assert colour == "#d68910"

    def test_unknown_tag_is_low(self):
        label, colour = _priority(["SOMETHING_UNKNOWN"])
        assert "LOW" in label
        assert colour == "#4a4a6a"

    def test_empty_tags_is_low(self):
        label, _ = _priority([])
        assert "LOW" in label

    def test_high_beats_medium_when_mixed(self):
        mixed = list(HIGH_PRIORITY_TAGS)[:1] + list(MEDIUM_PRIORITY_TAGS)[:1]
        label, _ = _priority(mixed)
        assert "HIGH" in label

    def test_returns_tuple_of_two_strings(self):
        result = _priority(["VM"])
        assert isinstance(result, tuple) and len(result) == 2
        assert all(isinstance(x, str) for x in result)
