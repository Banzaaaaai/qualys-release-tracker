"""
Qualys Release Notes Tracker
Scrapes https://www.qualys.com/documentation/release-notes daily,
diffs against the previous snapshot, and sends email notifications
for any new releases.
"""

import os
import json
import smtplib
import hashlib
import logging
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import requests
from bs4 import BeautifulSoup

# ── Config ────────────────────────────────────────────────────────────────────
RELEASE_NOTES_URL = "https://www.qualys.com/documentation/release-notes"
SNAPSHOT_FILE     = Path("snapshot.json")
LOG_LEVEL         = os.getenv("LOG_LEVEL", "INFO")

SMTP_HOST     = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT     = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER     = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
EMAIL_TO      = os.getenv("EMAIL_TO", "")
FORCE_NOTIFY  = os.getenv("FORCE_NOTIFY", "false").lower() == "true"

HIGH_PRIORITY_TAGS   = {"VM", "VMDR", "PC", "API", "VMDR OT"}
MEDIUM_PRIORITY_TAGS = {"CA", "ETM", "CSAM", "GAV", "PM", "EDR", "FIM"}

REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

logging.basicConfig(
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    level=getattr(logging, LOG_LEVEL),
)
log = logging.getLogger(__name__)


def fetch_releases() -> list:
    log.info("Fetching %s", RELEASE_NOTES_URL)
    resp = requests.get(RELEASE_NOTES_URL, timeout=30, headers=REQUEST_HEADERS)
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "lxml")
    releases      = []
    current_month = "Unknown"

    for li in soup.select("li"):
        css_classes = li.get("class", [])

        if "category-header" in css_classes:
            current_month = li.get_text(strip=True)
            continue

        if "releasenotes-item" not in css_classes:
            continue

        anchor = li.find("a", href=True)
        if not anchor:
            continue

        title = anchor.get_text(strip=True)
        url   = anchor["href"]
        tags  = [d.get_text(strip=True) for d in li.select("div[title]")]
        key   = hashlib.sha1(url.encode()).hexdigest()[:12]

        releases.append({
            "key":         key,
            "url":         url,
            "title":       title,
            "tags":        tags,
            "month_label": current_month,
        })

    log.info("Parsed %d releases from the page", len(releases))
    return releases


def load_snapshot() -> dict:
    if SNAPSHOT_FILE.exists():
        return json.loads(SNAPSHOT_FILE.read_text())
    return {}


def save_snapshot(releases: list) -> None:
    index = {r["key"]: r for r in releases}
    SNAPSHOT_FILE.write_text(json.dumps(index, indent=2))
    log.info("Snapshot saved (%d entries)", len(index))


def find_new_releases(current: list, snapshot: dict) -> list:
    new = [r for r in current if r["key"] not in snapshot]
    log.info("%d new release(s) detected", len(new))
    return new


def _priority(tags: list) -> tuple:
    tag_set = set(tags)
    if tag_set & HIGH_PRIORITY_TAGS:
        return "🔴 HIGH",   "#c0392b"
    if tag_set & MEDIUM_PRIORITY_TAGS:
        return "🟡 MEDIUM", "#d68910"
    return "🔵 OTHER",      "#1a5276"


def _tag_badge(tag: str, colour: str) -> str:
    return (
        f'<span style="display:inline-block;padding:2px 8px;margin:2px 2px 2px 0;'
        f'border-radius:3px;background:{colour};color:#fff;'
        f'font-size:11px;font-weight:700;">{tag}</span>'
    )


def build_html_email(new_releases: list, run_date: str) -> str:
    rows = ""
    for r in new_releases:
        priority_label, colour = _priority(r["tags"])
        badges = "".join(_tag_badge(t, colour) for t in r["tags"])
        rows += f"""
        <tr>
          <td style="padding:12px 10px;border-bottom:1px solid #eee;vertical-align:top;">
            <a href="{r['url']}" style="color:#1a5276;font-weight:600;text-decoration:none;font-size:14px;">{r['title']}</a><br>
            <span style="color:#aaa;font-size:11px;">{r['month_label']}</span>
          </td>
          <td style="padding:12px 10px;border-bottom:1px solid #eee;vertical-align:top;">{badges}</td>
          <td style="padding:12px 10px;border-bottom:1px solid #eee;vertical-align:top;font-size:12px;white-space:nowrap;">{priority_label}</td>
        </tr>"""

    count  = len(new_releases)
    suffix = "s" if count != 1 else ""
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"></head>
<body style="font-family:Arial,sans-serif;background:#f4f6f9;margin:0;padding:20px;">
<div style="max-width:800px;margin:0 auto;background:#fff;border-radius:6px;box-shadow:0 2px 8px rgba(0,0,0,.09);overflow:hidden;">
  <div style="background:#1a3a5c;padding:24px 28px;">
    <h1 style="margin:0;color:#fff;font-size:20px;">🔔 Qualys Release Notes — {count} New Release{suffix} Detected</h1>
    <p style="margin:8px 0 0;color:#adc8e6;font-size:13px;">
      Detected on {run_date} &nbsp;·&nbsp;
      <a href="{RELEASE_NOTES_URL}" style="color:#adc8e6;">View full release notes ↗</a>
    </p>
  </div>
  <div style="padding:20px 28px;">
    <table style="width:100%;border-collapse:collapse;font-size:13px;">
      <thead>
        <tr style="background:#f0f4f8;">
          <th style="padding:10px;text-align:left;color:#444;font-weight:700;border-bottom:2px solid #dde3ea;width:50%;">Release</th>
          <th style="padding:10px;text-align:left;color:#444;font-weight:700;border-bottom:2px solid #dde3ea;width:35%;">Modules</th>
          <th style="padding:10px;text-align:left;color:#444;font-weight:700;border-bottom:2px solid #dde3ea;width:15%;">Priority</th>
        </tr>
      </thead>
      <tbody>{rows}</tbody>
    </table>
  </div>
  <div style="padding:4px 28px 18px;font-size:11px;color:#888;line-height:1.8;">
    <strong>Priority tiers:</strong> 🔴 HIGH = VM / VMDR / PC / API / VMDR OT &nbsp;|&nbsp; 🟡 MEDIUM = CA / ETM / CSAM / GAV / PM / EDR / FIM &nbsp;|&nbsp; 🔵 OTHER
  </div>
  <div style="background:#f0f4f8;padding:12px 28px;font-size:11px;color:#aaa;text-align:center;">
    Automated by <strong>qualys-release-tracker</strong> · GitHub Actions
  </div>
</div>
</body></html>"""


def send_email(new_releases: list) -> None:
    if not SMTP_USER or not SMTP_PASSWORD or not EMAIL_TO:
        log.error("Email credentials not configured — skipping notification")
        return

    run_date  = datetime.now(timezone.utc).strftime("%d %B %Y")
    count     = len(new_releases)
    suffix    = "s" if count != 1 else ""
    subject   = f"[Qualys Tracker] {count} new release{suffix} detected — {run_date}"
    html_body = build_html_email(new_releases, run_date)

    lines = [f"Qualys Release Tracker — {count} new release{suffix} on {run_date}", ""]
    for r in new_releases:
        lines += [f"• {r['title']}  [{', '.join(r['tags'])}]", f"  {r['url']}", ""]
    text_body = "\n".join(lines)

    recipients = [e.strip() for e in EMAIL_TO.split(",") if e.strip()]
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = SMTP_USER
    msg["To"]      = ", ".join(recipients)
    msg.attach(MIMEText(text_body, "plain"))
    msg.attach(MIMEText(html_body, "html"))

    log.info("Connecting to %s:%s", SMTP_HOST, SMTP_PORT)
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        server.ehlo()
        server.starttls()
        server.login(SMTP_USER, SMTP_PASSWORD)
        server.sendmail(SMTP_USER, recipients, msg.as_string())
    log.info("Email sent → %s", recipients)


def main():
    log.info("=== Qualys Release Tracker starting ===")
    current  = fetch_releases()
    snapshot = load_snapshot()
    new      = find_new_releases(current, snapshot)

    if new or FORCE_NOTIFY:
        if FORCE_NOTIFY and not new:
            log.info("FORCE_NOTIFY=true — sending sample of 5 latest entries")
            new = current[:5]
        log.info("Sending notification for %d release(s)", len(new))
        send_email(new)
    else:
        log.info("No new releases — nothing to report")

    save_snapshot(current)
    log.info("=== Done ===")


if __name__ == "__main__":
    main()
