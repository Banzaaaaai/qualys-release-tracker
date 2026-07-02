"""
Qualys Release Notes Tracker — scraper.py
==========================================
Improvements vs v1:
  • Retry logic with exponential backoff       (fetch_page)
  • Failure notification email                 (main try/except → send_failure_email)
  • Snapshot integrity check                   (load_snapshot)
  • Staleness detection                        (check_staleness)
  • Run log in the repo                        (append_run_log)
  • GitHub Pages status badge                  (write_badge)
  • Monthly digest summary                     (send_monthly_digest, MONTHLY_DIGEST env)
  • Snapshot size guard + auto-archive         (snapshot_size_guard)
"""

import hashlib
import json
import logging
import os
import smtplib
import sys
import time
import traceback
from datetime import datetime, timezone, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import requests
from bs4 import BeautifulSoup

# ── Config ────────────────────────────────────────────────────────────────────
RELEASE_NOTES_URL = "https://www.qualys.com/documentation/release-notes"
SNAPSHOT_FILE     = Path("snapshot.json")
SNAPSHOT_ARCHIVE  = Path("snapshot_archive.json")
RUN_LOG_FILE      = Path("run_log.json")
BADGE_FILE        = Path("badge.json")
LOG_LEVEL         = os.getenv("LOG_LEVEL", "INFO")

SMTP_HOST     = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT     = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER     = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
EMAIL_TO      = os.getenv("EMAIL_TO", "")
FORCE_NOTIFY  = os.getenv("FORCE_NOTIFY", "false").lower() == "true"
MONTHLY_DIGEST = os.getenv("MONTHLY_DIGEST", "false").lower() == "true"

# Staleness: alert if no new release detected in this many days
STALENESS_DAYS = 7
# Archive entries older than this many days when snapshot exceeds WARN_BYTES
ARCHIVE_AFTER_DAYS = 730          # 2 years
SNAPSHOT_WARN_BYTES = 1_000_000   # 1 MB

HIGH_PRIORITY_TAGS   = {"VM", "VMDR", "PC", "API", "CA", "CSAM", "GAV", "Conn", "TC", "CRA", "CS", "PA"}
MEDIUM_PRIORITY_TAGS = {"VMDR OT", "ETM", "PM", "EDR", "FIM"}
LOW_PRIORITY_TAGS    = {"ID"}

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


# ── 1. Fetch with exponential backoff ────────────────────────────────────────

def fetch_page(url: str, max_retries: int = 3) -> str:
    """GET url with exponential backoff: 2s → 4s → 8s between attempts."""
    last_exc: Exception | None = None
    for attempt in range(max_retries):
        try:
            resp = requests.get(url, timeout=30, headers=REQUEST_HEADERS)
            resp.raise_for_status()
            log.info("Fetched %s — %d bytes", url, len(resp.text))
            return resp.text
        except requests.RequestException as exc:
            last_exc = exc
            wait = 2 ** (attempt + 1)          # 2, 4, 8
            if attempt < max_retries - 1:
                log.warning("Attempt %d/%d failed (%s). Retrying in %ds…",
                            attempt + 1, max_retries, exc, wait)
                time.sleep(wait)
    raise RuntimeError(
        f"All {max_retries} fetch attempts failed for {url}"
    ) from last_exc


# ── Parse ────────────────────────────────────────────────────────────────────

def fetch_releases() -> list[dict]:
    html = fetch_page(RELEASE_NOTES_URL)
    return parse_releases(html)


def parse_releases(html: str) -> list[dict]:
    """Parse release list. Separated so unit tests can call it directly."""
    soup = BeautifulSoup(html, "lxml")
    releases: list[dict] = []
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


# ── 2. Snapshot — load with integrity check ──────────────────────────────────

def load_snapshot() -> dict:
    """Load snapshot.json and validate its structure. Returns {} on missing file."""
    if not SNAPSHOT_FILE.exists():
        log.info("No snapshot found — all releases treated as new on first run")
        return {}

    raw = SNAPSHOT_FILE.read_text(encoding="utf-8")

    # Integrity check: valid JSON?
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"snapshot.json is corrupted (invalid JSON): {exc}\n"
            "Delete it and rerun to rebuild from scratch."
        ) from exc

    # Integrity check: expected structure?
    if not isinstance(data, dict):
        raise RuntimeError(
            f"snapshot.json must contain a JSON object, got {type(data).__name__}."
        )
    if data and not all(isinstance(k, str) and isinstance(v, dict)
                        for k, v in data.items()):
        raise RuntimeError(
            "snapshot.json structure invalid — expected {str: dict, …}."
        )

    # Integrity check: suspiciously small (possible truncation)?
    if len(raw) > 100 and len(data) == 0:
        raise RuntimeError(
            "snapshot.json is non-empty but parsed to zero entries — possible truncation."
        )

    log.info("Snapshot loaded — %d known entries", len(data))
    return data


def save_snapshot(releases: list[dict]) -> None:
    index = {r["key"]: r for r in releases}
    SNAPSHOT_FILE.write_text(json.dumps(index, indent=2, ensure_ascii=False))
    log.info("Snapshot saved (%d entries)", len(index))
    return index


# ── 3. Snapshot size guard + archive ─────────────────────────────────────────

def snapshot_size_guard(snapshot: dict) -> dict:
    """
    If snapshot.json exceeds SNAPSHOT_WARN_BYTES, move entries older than
    ARCHIVE_AFTER_DAYS into snapshot_archive.json and return the trimmed dict.
    """
    size = SNAPSHOT_FILE.stat().st_size if SNAPSHOT_FILE.exists() else 0
    if size <= SNAPSHOT_WARN_BYTES:
        return snapshot

    log.warning(
        "snapshot.json is %.2f MB — archiving entries older than %d days.",
        size / 1_048_576, ARCHIVE_AFTER_DAYS
    )
    cutoff = datetime.now(timezone.utc) - timedelta(days=ARCHIVE_AFTER_DAYS)
    keep, archive = {}, {}

    for key, entry in snapshot.items():
        detected = entry.get("detected_at", "")
        try:
            dt = datetime.fromisoformat(detected)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            (archive if dt < cutoff else keep)[key] = entry
        except (ValueError, TypeError):
            keep[key] = entry         # no timestamp → keep

    if archive:
        existing: dict = {}
        if SNAPSHOT_ARCHIVE.exists():
            try:
                existing = json.loads(SNAPSHOT_ARCHIVE.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                pass
        existing.update(archive)
        SNAPSHOT_ARCHIVE.write_text(
            json.dumps(existing, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        log.info(
            "Archived %d old entries → %s. Keeping %d active entries.",
            len(archive), SNAPSHOT_ARCHIVE, len(keep)
        )

    return keep


# ── 4. Diff ──────────────────────────────────────────────────────────────────

def find_new_releases(current: list[dict], snapshot: dict) -> list[dict]:
    new = [r for r in current if r["key"] not in snapshot]
    log.info("%d new release(s) detected", len(new))
    return new


# ── 5. Staleness detection ───────────────────────────────────────────────────

def check_staleness(snapshot: dict) -> bool:
    """
    Return True (and log a warning) if snapshot has entries but none were
    detected within the past STALENESS_DAYS days.
    Freshness is tracked via the 'detected_at' field written by append_run_log.
    """
    if not snapshot:
        return False

    timestamps = []
    for entry in snapshot.values():
        ts = entry.get("detected_at")
        if ts:
            try:
                dt = datetime.fromisoformat(ts)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                timestamps.append(dt)
            except (ValueError, TypeError):
                pass

    if not timestamps:
        return False                   # no timestamps yet → not stale

    latest = max(timestamps)
    age = (datetime.now(timezone.utc) - latest).days
    if age >= STALENESS_DAYS:
        log.warning(
            "STALENESS ALERT: No new release detected in %d days "
            "(last seen: %s). Parser may have broken.",
            age, latest.strftime("%Y-%m-%d")
        )
        return True
    return False


# ── 6. Run log ───────────────────────────────────────────────────────────────

def append_run_log(
    *,
    new_count: int,
    total_count: int,
    email_sent: bool,
    stale: bool,
    status: str,
    error: str = "",
    duration_s: float = 0.0,
) -> None:
    """Append a one-line JSON entry to run_log.json."""
    entries: list[dict] = []
    if RUN_LOG_FILE.exists():
        try:
            entries = json.loads(RUN_LOG_FILE.read_text(encoding="utf-8"))
            if not isinstance(entries, list):
                entries = []
        except json.JSONDecodeError:
            entries = []

    entries.append({
        "timestamp":   datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "status":      status,
        "new_releases": new_count,
        "total_tracked": total_count,
        "email_sent":  email_sent,
        "stale_alert": stale,
        "duration_s":  round(duration_s, 1),
        "error":       error,
    })

    # Keep last 365 entries
    entries = entries[-365:]
    RUN_LOG_FILE.write_text(json.dumps(entries, indent=2, ensure_ascii=False), encoding="utf-8")
    log.info("Run log updated (%d entries total)", len(entries))


# ── 7. GitHub Pages badge ────────────────────────────────────────────────────

def write_badge(total_count: int, status: str) -> None:
    """
    Write badge.json in Shields.io endpoint format.
    Add a GitHub Pages step in the workflow to expose this file,
    then use: https://img.shields.io/endpoint?url=<pages-url>/badge.json
    """
    color = "brightgreen" if status == "success" else "red"
    badge = {
        "schemaVersion": 1,
        "label":         "qualys tracker",
        "message":       f"{total_count} releases · {datetime.now(timezone.utc).strftime('%Y-%m-%d')}",
        "color":         color,
        "namedLogo":     "github-actions",
    }
    BADGE_FILE.write_text(json.dumps(badge, indent=2), encoding="utf-8")
    log.info("Badge written → %s", BADGE_FILE)


# ── Priority helpers ─────────────────────────────────────────────────────────

def _priority(tags: list[str]) -> tuple[str, str]:
    tag_set = set(tags)
    if tag_set & HIGH_PRIORITY_TAGS:
        return "🔴 HIGH",   "#c0392b"
    if tag_set & MEDIUM_PRIORITY_TAGS:
        return "🟡 MEDIUM", "#d68910"
    if tag_set & LOW_PRIORITY_TAGS:
        return "⚪ LOW",        "#4a4a6a"
    return "🔵 OTHER",      "#1a5276"


def _tag_badge(tag: str, colour: str) -> str:
    return (
        f'<span style="display:inline-block;padding:2px 8px;margin:2px 2px 2px 0;'
        f'border-radius:3px;background:{colour};color:#fff;'
        f'font-size:11px;font-weight:700;">{tag}</span>'
    )


# ── Email builders ───────────────────────────────────────────────────────────

def build_html_email(new_releases: list[dict], run_date: str) -> str:
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
<div style="max-width:800px;margin:0 auto;background:#fff;border-radius:6px;
            box-shadow:0 2px 8px rgba(0,0,0,.09);overflow:hidden;">
  <div style="background:#1a3a5c;padding:24px 28px;">
    <h1 style="margin:0;color:#fff;font-size:20px;">
      🔔 Qualys Release Notes — {count} New Release{suffix} Detected
    </h1>
    <p style="margin:8px 0 0;color:#adc8e6;font-size:13px;">
      Detected on {run_date} &nbsp;·&nbsp;
      <a href="{RELEASE_NOTES_URL}" style="color:#adc8e6;">View full release notes ↗</a>
    </p>
  </div>
  <div style="padding:20px 28px;">
    <table style="width:100%;border-collapse:collapse;font-size:13px;">
      <thead>
        <tr style="background:#f0f4f8;">
          <th style="padding:10px;text-align:left;color:#444;font-weight:700;
                     border-bottom:2px solid #dde3ea;width:50%;">Release</th>
          <th style="padding:10px;text-align:left;color:#444;font-weight:700;
                     border-bottom:2px solid #dde3ea;width:35%;">Modules</th>
          <th style="padding:10px;text-align:left;color:#444;font-weight:700;
                     border-bottom:2px solid #dde3ea;width:15%;">Priority</th>
        </tr>
      </thead>
      <tbody>{rows}</tbody>
    </table>
  </div>
  <div style="padding:4px 28px 18px;font-size:11px;color:#888;line-height:1.8;">
    <strong>Priority tiers:</strong>
    🔴 HIGH = VM / VMDR / PC / API / CA / CSAM / GAV &nbsp;|&nbsp;
    🟡 MEDIUM = VMDR OT / ETM / PM / EDR / FIM &nbsp;|&nbsp;
    🔵 OTHER
  </div>
  <div style="background:#f0f4f8;padding:12px 28px;font-size:11px;color:#aaa;text-align:center;">
    Automated by <strong>qualys-release-tracker</strong> · GitHub Actions
  </div>
</div>
</body></html>"""


def build_failure_email(error_summary: str) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"></head>
<body style="font-family:Arial,sans-serif;background:#f4f6f9;margin:0;padding:20px;">
<div style="max-width:680px;margin:0 auto;background:#fff;border-radius:6px;
            box-shadow:0 2px 8px rgba(0,0,0,.09);overflow:hidden;">
  <div style="background:#c0392b;padding:20px 28px;">
    <h1 style="margin:0;color:#fff;font-size:18px;">⚠️ Qualys Tracker — Run Failed</h1>
    <p style="margin:6px 0 0;color:#f5b7b1;font-size:13px;">{ts}</p>
  </div>
  <div style="padding:24px 28px;">
    <p style="color:#444;font-size:14px;">
      The Qualys Release Tracker encountered an error during its scheduled run.
      <strong>No snapshot was updated</strong> and <strong>no release email was sent</strong>.
    </p>
    <div style="background:#fdf2f2;border-left:4px solid #c0392b;padding:14px 18px;
                border-radius:4px;font-family:monospace;font-size:12px;
                color:#922b21;white-space:pre-wrap;word-break:break-word;">
{error_summary}
    </div>
    <p style="color:#888;font-size:12px;margin-top:18px;">
      Check the
      <a href="https://github.com/Banzaaaaai/qualys-release-tracker/actions"
         style="color:#1a5276;">GitHub Actions log</a>
      for full details. The next scheduled run will attempt recovery automatically.
    </p>
  </div>
</div>
</body></html>"""


def build_staleness_email(days: int, total: int) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"></head>
<body style="font-family:Arial,sans-serif;background:#f4f6f9;margin:0;padding:20px;">
<div style="max-width:680px;margin:0 auto;background:#fff;border-radius:6px;
            box-shadow:0 2px 8px rgba(0,0,0,.09);overflow:hidden;">
  <div style="background:#d68910;padding:20px 28px;">
    <h1 style="margin:0;color:#fff;font-size:18px;">🟡 Qualys Tracker — Staleness Alert</h1>
    <p style="margin:6px 0 0;color:#fdebd0;font-size:13px;">{ts}</p>
  </div>
  <div style="padding:24px 28px;color:#444;font-size:14px;line-height:1.7;">
    <p>
      No new Qualys release has been detected in the past <strong>{days} days</strong>.
      This could mean:
    </p>
    <ul>
      <li>Qualys has not published any releases recently (possible, but worth verifying).</li>
      <li>The page structure changed and the parser is no longer extracting entries correctly.</li>
      <li>The tracker's HTTP fetch is silently failing or returning an unexpected page.</li>
    </ul>
    <p>
      <strong>{total}</strong> releases are currently in the snapshot.
      Please verify the
      <a href="{RELEASE_NOTES_URL}" style="color:#1a5276;">Qualys release notes page</a>
      and the
      <a href="https://github.com/Banzaaaaai/qualys-release-tracker/actions"
         style="color:#1a5276;">GitHub Actions log</a>.
    </p>
  </div>
</div>
</body></html>"""


def build_monthly_digest_email(
    run_log: list[dict], snapshot: dict, target_month: datetime | None = None
) -> str:
    now = datetime.now(timezone.utc)
    target = target_month or now
    month_name = target.strftime("%B %Y")

    month_start = target.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    month_end = (
        month_start.replace(year=month_start.year + 1, month=1)
        if month_start.month == 12
        else month_start.replace(month=month_start.month + 1)
    )
    ms_iso = month_start.isoformat(timespec="seconds")
    me_iso = month_end.isoformat(timespec="seconds")

    month_entries = [
        e for e in run_log
        if ms_iso <= e.get("timestamp", "") < me_iso
    ]
    total_runs    = len(month_entries)
    success_runs  = sum(1 for e in month_entries if e.get("status") == "success")
    new_found     = sum(e.get("new_releases", 0) for e in month_entries)
    emails_sent   = sum(1 for e in month_entries if e.get("email_sent"))
    stale_alerts  = sum(1 for e in month_entries if e.get("stale_alert"))

    # Modules actually detected/released this calendar month
    released = sorted(
        (e for e in snapshot.values()
         if ms_iso <= e.get("detected_at", "") < me_iso),
        key=lambda e: e.get("detected_at", ""),
    )
    released_rows = ""
    for r in released:
        priority_label, colour = _priority(r.get("tags", []))
        badges = "".join(_tag_badge(t, colour) for t in r.get("tags", []))
        released_rows += f"""
        <tr>
          <td style="padding:10px 12px;border-bottom:1px solid #eee;vertical-align:top;">
            <a href="{r.get('url','')}" style="color:#1a5276;font-weight:600;text-decoration:none;font-size:13px;">{r.get('title','')}</a>
          </td>
          <td style="padding:10px 12px;border-bottom:1px solid #eee;vertical-align:top;">{badges}</td>
          <td style="padding:10px 12px;border-bottom:1px solid #eee;vertical-align:top;font-size:12px;white-space:nowrap;">{priority_label}</td>
        </tr>"""
    if not released_rows:
        released_rows = '<tr><td colspan="3" style="padding:10px 12px;color:#888;">No modules detected this month.</td></tr>'
    released_section = f"""
    <h3 style="font-size:14px;color:#1a3a5c;margin:22px 0 10px;">
      Modules released &mdash; {month_name} ({len(released)})
    </h3>
    <table style="width:100%;border-collapse:collapse;font-size:13px;">
      <thead>
        <tr style="background:#f0f4f8;">
          <th style="padding:8px 12px;text-align:left;color:#444;font-weight:700;border-bottom:2px solid #dde3ea;width:55%;">Module</th>
          <th style="padding:8px 12px;text-align:left;color:#444;font-weight:700;border-bottom:2px solid #dde3ea;width:30%;">Tags</th>
          <th style="padding:8px 12px;text-align:left;color:#444;font-weight:700;border-bottom:2px solid #dde3ea;width:15%;">Priority</th>
        </tr>
      </thead>
      <tbody>{released_rows}</tbody>
    </table>"""

    # Priority breakdown across snapshot
    high, medium, other = 0, 0, 0
    for entry in snapshot.values():
        tags = set(entry.get("tags", []))
        if tags & HIGH_PRIORITY_TAGS:
            high += 1
        elif tags & MEDIUM_PRIORITY_TAGS:
            medium += 1
        else:
            other += 1

    def stat_cell(value, label, colour="#1a3a5c"):
        return f"""
        <td style="text-align:center;padding:16px 10px;">
          <div style="font-size:28px;font-weight:700;color:{colour};">{value}</div>
          <div style="font-size:11px;color:#888;margin-top:4px;">{label}</div>
        </td>"""

    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"></head>
<body style="font-family:Arial,sans-serif;background:#f4f6f9;margin:0;padding:20px;">
<div style="max-width:680px;margin:0 auto;background:#fff;border-radius:6px;
            box-shadow:0 2px 8px rgba(0,0,0,.09);overflow:hidden;">
  <div style="background:#1a3a5c;padding:24px 28px;">
    <h1 style="margin:0;color:#fff;font-size:20px;">
      📊 Qualys Tracker — Monthly Digest
    </h1>
    <p style="margin:6px 0 0;color:#adc8e6;font-size:13px;">{month_name}</p>
  </div>

  <div style="padding:20px 28px;">
    <p style="color:#555;font-size:13px;margin-top:0;">
      Summary of tracker activity over the past 30 days.
    </p>
    <table style="width:100%;border-collapse:collapse;
                  background:#f8fafc;border-radius:6px;overflow:hidden;">
      <tr>
        {stat_cell(total_runs, "Runs")}
        {stat_cell(success_runs, "Successful")}
        {stat_cell(new_found, "New Releases Found", "#27ae60")}
        {stat_cell(emails_sent, "Emails Sent")}
        {stat_cell(stale_alerts, "Staleness Alerts", "#d68910" if stale_alerts else "#1a3a5c")}
      </tr>
    </table>

    <h3 style="font-size:14px;color:#1a3a5c;margin:22px 0 10px;">
      Snapshot priority breakdown ({len(snapshot)} total entries)
    </h3>
    <table style="width:100%;border-collapse:collapse;font-size:13px;">
      <tr>
        <td style="padding:8px 12px;background:#fdf2f2;border-radius:4px;
                   font-weight:700;color:#c0392b;">🔴 HIGH</td>
        <td style="padding:8px 12px;text-align:right;font-weight:700;">{high}</td>
      </tr>
      <tr>
        <td style="padding:8px 12px;background:#fef9e7;border-radius:4px;
                   font-weight:700;color:#d68910;">🟡 MEDIUM</td>
        <td style="padding:8px 12px;text-align:right;font-weight:700;">{medium}</td>
      </tr>
      <tr>
        <td style="padding:8px 12px;background:#eaf4fc;border-radius:4px;
                   font-weight:700;color:#1a5276;">🔵 OTHER</td>
        <td style="padding:8px 12px;text-align:right;font-weight:700;">{other}</td>
      </tr>
    </table>
    {released_section}
  </div>

  <div style="background:#f0f4f8;padding:12px 28px;font-size:11px;color:#aaa;text-align:center;">
    Automated by <strong>qualys-release-tracker</strong> · GitHub Actions
  </div>
</div>
</body></html>"""


# ── Send helpers ─────────────────────────────────────────────────────────────

def _send(subject: str, html_body: str, recipients: list[str] | None = None) -> None:
    if not SMTP_USER or not SMTP_PASSWORD or not EMAIL_TO:
        log.error("Email credentials not configured — skipping send")
        return
    to = recipients or [e.strip() for e in EMAIL_TO.split(",") if e.strip()]
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = SMTP_USER
    msg["To"]      = ", ".join(to)
    msg.attach(MIMEText(html_body, "html", "utf-8"))
    log.info("Connecting to %s:%s", SMTP_HOST, SMTP_PORT)
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        server.ehlo()
        server.starttls()
        server.login(SMTP_USER, SMTP_PASSWORD)
        server.sendmail(SMTP_USER, to, msg.as_bytes())
    log.info("Email sent → %s", to)


def send_release_email(new_releases: list[dict]) -> None:
    run_date = datetime.now(timezone.utc).strftime("%d %B %Y")
    count    = len(new_releases)
    suffix   = "s" if count != 1 else ""
    _send(
        f"[Qualys Tracker] {count} new release{suffix} detected — {run_date}",
        build_html_email(new_releases, run_date),
    )


def send_failure_email(error_summary: str) -> None:
    try:
        _send("[Qualys Tracker] ⚠️ Run failed", build_failure_email(error_summary))
    except Exception as exc:
        log.error("Could not send failure email: %s", exc)


def send_staleness_email(days: int, total: int) -> None:
    _send(
        f"[Qualys Tracker] 🟡 Staleness alert — no new release in {days} days",
        build_staleness_email(days, total),
    )


def send_monthly_digest(
    run_log: list[dict], snapshot: dict, target_month: datetime | None = None
) -> None:
    month_name = (target_month or datetime.now(timezone.utc)).strftime("%B %Y")
    _send(
        f"[Qualys Tracker] 📊 Monthly digest — {month_name}",
        build_monthly_digest_email(run_log, snapshot, target_month),
    )


# ── Main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    t_start = time.monotonic()
    log.info("=== Qualys Release Tracker starting ===")

    # Handle monthly digest mode — send stats then exit
    if MONTHLY_DIGEST:
        log.info("MONTHLY_DIGEST=true — sending digest and exiting")
        snapshot = load_snapshot() if SNAPSHOT_FILE.exists() else {}
        run_log: list[dict] = []
        if RUN_LOG_FILE.exists():
            try:
                run_log = json.loads(RUN_LOG_FILE.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                pass
        digest_month_env = os.getenv("DIGEST_MONTH", "").strip()
        target_month = None
        if digest_month_env:
            target_month = datetime.strptime(digest_month_env, "%Y-%m").replace(
                tzinfo=timezone.utc
            )
            log.info("DIGEST_MONTH=%s — targeting that month", digest_month_env)
        send_monthly_digest(run_log, snapshot, target_month)
        return

    status     = "success"
    error_msg  = ""
    new: list[dict] = []
    snapshot: dict  = {}
    email_sent = False
    stale      = False

    try:
        # Load + validate snapshot
        try:
            snapshot = load_snapshot()
        except RuntimeError as exc:
            raise RuntimeError(f"Snapshot integrity check failed: {exc}") from exc

        # Guard snapshot size
        snapshot = snapshot_size_guard(snapshot)

        # Fetch live releases
        current = fetch_releases()

        # Diff
        new = find_new_releases(current, snapshot)

        # Stamp detected_at on new entries so staleness tracking works
        now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
        for r in new:
            r["detected_at"] = now_iso

        # Staleness check
        merged_snapshot = {r["key"]: r for r in current}
        for key, entry in snapshot.items():
            if key not in merged_snapshot:
                merged_snapshot[key] = entry
        stale = check_staleness(merged_snapshot)

        # Email
        if new or FORCE_NOTIFY:
            sample = new if new else current[:5]
            send_release_email(sample)
            email_sent = True

        if stale and not new:
            send_staleness_email(STALENESS_DAYS, len(snapshot))

        # Save updated snapshot (carry forward detected_at for known entries)
        for r in current:
            if r["key"] in snapshot and "detected_at" not in r:
                r["detected_at"] = snapshot[r["key"]].get("detected_at", now_iso)
        save_snapshot(current)

    except Exception as exc:
        status    = "failure"
        error_msg = traceback.format_exc()
        log.error("Run failed:\n%s", error_msg)
        send_failure_email(error_msg)

    finally:
        duration = time.monotonic() - t_start

        # Run log
        append_run_log(
            new_count    = len(new),
            total_count  = len(snapshot),
            email_sent   = email_sent,
            stale        = stale,
            status       = status,
            error        = error_msg[:500] if error_msg else "",
            duration_s   = duration,
        )

        # Badge
        write_badge(len(snapshot), status)

        log.info("=== Done in %.1fs (status=%s) ===", duration, status)

    if status == "failure":
        sys.exit(1)


if __name__ == "__main__":
    main()
