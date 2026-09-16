"""Offline regression tests for persisted state and notification orchestration."""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock

import pytest
sys.path.insert(0, str(Path(__file__).parent.parent))
import scraper


def release(key="known", **extra):
    return dict(key=key, url=f"https://example.invalid/{key}.pdf", title=key,
                tags=["VM"], month_label="January 2020", **extra)


@pytest.fixture
def tracker(tmp_path, monkeypatch):
    for name, filename in [("SNAPSHOT_FILE", "snapshot.json"),
                           ("SNAPSHOT_ARCHIVE", "archive.json"),
                           ("RUN_LOG_FILE", "log.json"), ("BADGE_FILE", "badge.json")]:
        monkeypatch.setattr(scraper, name, tmp_path / filename)
    monkeypatch.setattr(scraper, "MONTHLY_DIGEST", False)
    monkeypatch.setattr(scraper, "FORCE_NOTIFY", False)
    for name in ["send_release_email", "send_staleness_email", "send_failure_email"]:
        monkeypatch.setattr(scraper, name, Mock())
    current = Mock(return_value=[release()])
    monkeypatch.setattr(scraper, "fetch_releases", current)
    scraper.save_snapshot([release(detected_at="2020-01-01T00:00:00+00:00",
                                  details={"features": [{"heading": "Feature"}]})])
    return current


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_empty_scrape_preserves_snapshot_and_records_failure(tracker):
    before = scraper.SNAPSHOT_FILE.read_bytes()
    tracker.return_value = []
    with pytest.raises(SystemExit) as exc:
        scraper.main()
    assert exc.value.code == 1
    assert scraper.SNAPSHOT_FILE.read_bytes() == before
    assert read(scraper.RUN_LOG_FILE)[-1]["status"] == "failure"
    assert read(scraper.BADGE_FILE)["color"] == "red"


def test_metadata_survives_and_stale_email_is_suppressed_on_next_run(tracker):
    before = read(scraper.SNAPSHOT_FILE)
    scraper.main()
    assert read(scraper.SNAPSHOT_FILE) == before
    scraper.send_staleness_email.assert_called_once()
    first = read(scraper.RUN_LOG_FILE)[-1]
    assert first["stale_alert"] and first["email_sent"]
    scraper.main()
    scraper.send_staleness_email.assert_called_once()
    second = read(scraper.RUN_LOG_FILE)[-1]
    assert not second["stale_alert"] and not second["email_sent"]


def test_removed_release_remains_known_and_totals_include_new_release(tracker):
    tracker.return_value = [release("new")]
    scraper.main()
    assert set(read(scraper.SNAPSHOT_FILE)) == {"known", "new"}
    assert read(scraper.RUN_LOG_FILE)[-1]["total_tracked"] == 2
    assert read(scraper.BADGE_FILE)["message"].startswith("2 releases")


def test_archived_release_is_not_announced_or_restored(tracker, monkeypatch):
    monkeypatch.setattr(scraper, "SNAPSHOT_WARN_BYTES", 0)
    scraper.main()
    assert read(scraper.SNAPSHOT_FILE) == {}
    assert "known" in read(scraper.SNAPSHOT_ARCHIVE)
    scraper.main()
    scraper.send_release_email.assert_not_called()
    assert read(scraper.SNAPSHOT_FILE) == {}
    assert read(scraper.RUN_LOG_FILE)[-1]["total_tracked"] == 1


def test_corrupt_archive_fails_without_overwriting_it(tracker):
    scraper.SNAPSHOT_ARCHIVE.write_text("broken", encoding="utf-8")
    with pytest.raises(SystemExit):
        scraper.main()
    assert scraper.SNAPSHOT_ARCHIVE.read_text() == "broken"
    scraper.send_release_email.assert_not_called()


@pytest.mark.parametrize("now,expected", [("2026-01-01", "December 2025"),
                                         ("2026-03-01", "February 2026")])
def test_digest_defaults_to_completed_month(monkeypatch, now, expected):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.fromisoformat(now).replace(tzinfo=timezone.utc)
    monkeypatch.setattr(scraper, "datetime", Clock)
    send = Mock()
    monkeypatch.setattr(scraper, "_send", send)
    scraper.send_monthly_digest([], {})
    subject, body = send.call_args.args
    assert expected in subject and expected in body
    assert expected in scraper.build_monthly_digest_email([], {})
    explicit = datetime(2024, 5, 1, tzinfo=timezone.utc)
    scraper.send_monthly_digest([], {}, explicit)
    assert "May 2024" in send.call_args.args[0]
