"""Offline delivery, retry and real-world markup regressions."""
import smtplib
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, Mock

import pytest
import scraper


@pytest.fixture
def smtp(monkeypatch):
    for name, value in {"SMTP_USER": "sender@example.invalid", "SMTP_PASSWORD": "test",
                        "EMAIL_TO": "one@example.invalid, two@example.invalid"}.items():
        monkeypatch.setattr(scraper, name, value)
    factory = MagicMock()
    factory.return_value.__enter__.return_value.sendmail.return_value = {}
    monkeypatch.setattr(scraper.smtplib, "SMTP", factory)
    return factory


@pytest.mark.parametrize("setting", ["SMTP_USER", "SMTP_PASSWORD", "EMAIL_TO"])
def test_missing_configuration_fails_before_connecting(smtp, monkeypatch, setting):
    monkeypatch.setattr(scraper, setting, "")
    with pytest.raises(RuntimeError):
        scraper._send("Test", "body")
    smtp.assert_not_called()


def test_blank_recipients_fail(smtp, monkeypatch):
    monkeypatch.setattr(scraper, "EMAIL_TO", " , ")
    with pytest.raises(RuntimeError):
        scraper._send("Test", "body")
    smtp.assert_not_called()


def test_delivery_uses_timeout_verified_tls_and_all_recipients(smtp):
    scraper._send("Test", "body")
    assert smtp.call_args.kwargs["timeout"] == 30
    server = smtp.return_value.__enter__.return_value
    context = server.starttls.call_args.kwargs["context"]
    assert context.check_hostname
    assert server.sendmail.call_args.args[1] == ["one@example.invalid", "two@example.invalid"]


def test_partial_recipient_refusal_is_a_failure(smtp):
    server = smtp.return_value.__enter__.return_value
    server.sendmail.return_value = {"two@example.invalid": (550, b"Rejected")}
    with pytest.raises(smtplib.SMTPRecipientsRefused):
        scraper._send("Test", "body")


def test_connection_failure_propagates(smtp):
    smtp.side_effect = TimeoutError("SMTP timed out")
    with pytest.raises(TimeoutError):
        scraper._send("Test", "body")


def test_detail_summary_stops_at_next_heading(monkeypatch):
    monkeypatch.setattr(scraper, "fetch_page", lambda _: """
        <h1>Release</h1><p>September 2026</p>
        <h2>Without summary</h2><h2>With summary</h2>
        <div><p>Its own summary</p></div>""")
    details = scraper.fetch_release_details("https://example.invalid")
    assert details["features"] == [
        {"heading": "Without summary", "summary": ""},
        {"heading": "With summary", "summary": "Its own summary"}]


def test_relative_urls_fragments_and_invalid_links():
    parsed = scraper.parse_releases('''
        <li class="releasenotes-item"><a href="/notes/a#first">A</a></li>
        <li class="releasenotes-item"><a href="https://www.qualys.com/notes/a#second">A again</a></li>
        <li class="releasenotes-item"><a href="javascript:void(0)">Bad</a></li>
    ''')
    assert len(parsed) == 1
    assert parsed[0]["url"] == "https://www.qualys.com/notes/a"
    assert len(scraper.find_new_releases(parsed * 2, {})) == 1


@pytest.fixture
def fetch_details(monkeypatch):
    fetch = Mock(return_value={"features": [{"heading": "Feature", "summary": "Works"}]})
    monkeypatch.setattr(scraper, "fetch_release_details", fetch)
    monkeypatch.setattr(scraper.time, "sleep", Mock())
    return fetch


def test_failed_enrichment_retries_after_cooldown(fetch_details):
    entry = {"url": "https://example.invalid/release"}
    fetch_details.side_effect = RuntimeError("Unavailable")
    scraper.enrich_releases([entry])
    assert entry["enrichment"]["status"] == "failed"
    scraper.enrich_releases([entry])
    assert fetch_details.call_count == 1
    entry["enrichment"]["last_attempt_at"] = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    fetch_details.side_effect = None
    scraper.enrich_releases([entry])
    assert entry["details"]
    assert entry["enrichment"]["attempts"] == 2
    scraper.enrich_releases([entry])
    assert fetch_details.call_count == 2


def test_enrichment_batch_and_attempt_limits(fetch_details):
    exhausted = {"url": "https://example.invalid/exhausted", "enrichment": {"attempts": 3}}
    pdf = {"url": "https://example.invalid/notes.PDF?download=1"}
    entries = [{"url": f"https://example.invalid/{i}"} for i in range(8)]
    scraper.enrich_releases([exhausted, pdf, *entries])
    assert fetch_details.call_count == scraper.ENRICHMENT_BATCH_SIZE
    assert "details" not in exhausted and "details" not in pdf
    scraper.enrich_releases(entries)
    assert all(e.get("details") for e in entries)


def test_empty_detail_page_remains_retryable(fetch_details):
    fetch_details.return_value = {"release_date": "", "features": [], "issues_fixed": [], "cves": []}
    entry = {"url": "https://example.invalid/release"}
    scraper.enrich_releases([entry])
    assert "details" not in entry
    assert entry["enrichment"]["status"] == "failed"
