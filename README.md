# Qualys Release Tracker

Tracker that monitors the [Qualys Suite Release Notes](https://www.qualys.com/documentation/release-notes) page and sends an HTML email notification whenever new releases are published.

Runs automatically twice a day — **08:45** and **16:00 Amsterdam time** — via GitHub Actions (DST-adjusted automatically).

---

## Features

| Feature | Details |
|---|---|
| **Retry logic** | 3 attempts with exponential backoff (2s → 4s → 8s) on HTTP failures |
| **Failure email** | Sends an alert email if the run crashes, so you know immediately |
| **Snapshot integrity** | Validates `snapshot.json` structure on every run; aborts if corrupt |
| **Staleness detection** | Warns by email if no new release is found for 7+ consecutive days |
| **DST auto-adjustment** | `dst_adjuster.yml` patches both daily crons each March/October — 08:45 & 16:00 Amsterdam year-round |
| **Run log** | Appends a JSON entry to `run_log.json` after every run (status, counts, duration) |
| **Status badge** | Writes `badge.json` (Shields.io endpoint format) after every run |
| **Release detail enrichment** | For each new release, fetches its detail page and extracts feature summaries, issues-fixed (by component), and referenced CVEs |
| **Mobile-friendly email** | Single-column stacked card layout (inline styles, no `@media`) so it renders cleanly in Gmail mobile |
| **Monthly digest** | First day of each month: sends a stats summary (runs, new releases, priority breakdown) |
| **Snapshot size guard** | When `snapshot.json` exceeds 1 MB, archives entries older than 2 years automatically |
| **Offline unit tests** | `tests/test_parser.py` — 37 tests covering parser and priority logic, no HTTP needed |

---

## How it works

1. **Scrapes** `qualys.com/documentation/release-notes` and parses every release entry (title, URL, module tags).
2. **Diffs** against `snapshot.json` to identify new entries.
3. **Fetches details** for each new (non-PDF) release — feature summaries, issues fixed, referenced CVEs — with a 1.5s delay between requests.
4. **Emails** a mobile-friendly HTML report: one card per release, with module badges, priority tiers, feature bullets, and an issues-fixed summary.
5. **Commits** the updated snapshot, run log, and badge back to the repo.

---

## Setup

### 1. Fork / clone this repo

### 2. Add GitHub Actions secrets

**Settings → Secrets and variables → Actions → New repository secret**

| Secret | Value |
|---|---|
| `SMTP_HOST` | `smtp.gmail.com` |
| `SMTP_PORT` | `587` |
| `SMTP_USER` | Your sending address |
| `SMTP_PASSWORD` | Gmail app password |
| `EMAIL_TO` | Recipients, comma-separated |
| `GH_PAT` | Personal Access Token with `workflow` scope (needed by `dst_adjuster.yml` to patch workflow files) |

#### Gmail app password
1. Enable 2FA on your Google account.
2. **Google Account → Security → App passwords**.
3. Create a password for "Mail / Other". Use the 16-character result as `SMTP_PASSWORD`.

#### GH_PAT (for DST adjuster)
1. **GitHub → Settings → Developer settings → Personal access tokens → Fine-grained**.
2. Scope: `Contents: Read & Write` + `Workflows: Read & Write` on this repo only.

### 3. Enable Actions

**Actions** tab → **Enable Actions** if prompted.

### 4. Test manually

**Actions → Qualys Release Tracker → Run workflow → `force_notify = true`**

---

## Priority tiers

| Tier | Module tags |
|---|---|
| 🔴 HIGH | `VM` `VMDR` `PC` `API` `CA` `CSAM` `GAV` `Conn` `TC` `CRA` `CS` `PA` `TAS` `WAS` |
| 🟡 MEDIUM | `ETM` `PM` `EDR` `FIM` `UD` |
| ⚪ LOW | Everything else (including `ID`, `VMDR OT`) |

---

## Files

| File | Purpose |
|---|---|
| `scraper.py` | Main scraper, diff, email, run log, badge |
| `snapshot.json` | Last-known state (auto-updated) |
| `snapshot_archive.json` | Entries older than 2 years (auto-created when needed) |
| `run_log.json` | Per-run audit log (auto-updated, last 365 entries) |
| `badge.json` | Shields.io endpoint — embed in README as a live badge |
| `requirements.txt` | Python dependencies |
| `.github/workflows/tracker.yml` | Twice-daily + monthly schedule |
| `.github/workflows/dst_adjuster.yml` | Automatic DST cron patching |
| `tests/test_parser.py` | Offline unit tests for parser + priority logic |

---

## Status badge

After the first run, add this to any README or dashboard:

```markdown
![Qualys Tracker](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/Banzaaaaai/qualys-release-tracker/main/badge.json)
```

---

## Monthly digest

Sent automatically on the 1st of each month. Includes: runs, successful runs, new releases found, emails sent, staleness alerts, and a priority breakdown of the full snapshot. Trigger manually via **Run workflow → `monthly_digest = true`**.

---

## Running tests locally

```bash
pip install -r requirements.txt
pytest tests/test_parser.py -v
```

---

## Local scraper run

```bash
pip install -r requirements.txt

export SMTP_HOST=smtp.gmail.com
export SMTP_PORT=587
export SMTP_USER=you@gmail.com
export SMTP_PASSWORD=yourapppassword
export EMAIL_TO=you@gmail.com

python scraper.py
```

Delete `snapshot.json` before the first local run to treat all current entries as new.

---

## DST adjuster testing

**Actions → DST Schedule Adjuster → Run workflow**

- `force_offset = 2` → sets CEST (summer, UTC+2) → crons `45 6 * * *` (08:45) and `0 14 * * *` (16:00)
- `force_offset = 1` → sets CET (winter, UTC+1) → crons `45 7 * * *` (08:45) and `0 15 * * *` (16:00)
