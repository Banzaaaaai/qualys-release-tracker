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
| **Offline tests** | 58 tests covering parsing, SMTP failures, enrichment retries, priority, snapshot preservation, archiving, staleness, and digest dates; no HTTP or email needed |
| **SMTP correctness** | Missing settings, empty recipient lists, connection failures, and partial recipient refusals fail the run; verified STARTTLS and a 30-second socket timeout |
| **Workflow concurrency** | Tracker and DST adjuster share a per-branch concurrency group; an active run is never cancelled by a newer run |
| **Enrichment retries** | Up to 5 HTML detail fetches per run, new releases first; failed enrichment retries after 24 hours, up to 3 attempts per release |

---

## How it works

1. **Scrapes** `qualys.com/documentation/release-notes` and parses every release entry (title, URL, module tags).
2. **Diffs** against `snapshot.json` to identify new entries.
3. **Fetches details** for up to 5 non-PDF releases — feature summaries, issues fixed, referenced CVEs — prioritizing new releases, then backfilling active history, with a 1.5s delay between detail fetches.
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
| 🟢 LOW | Everything else (including `ID`, `VMDR OT`) |

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

Sent automatically on the 1st of each month for the previous calendar month. Includes: runs, successful runs, new releases found, emails sent, staleness alerts actually sent, and a priority breakdown of active and archived releases. Trigger manually via **Run workflow → `monthly_digest = true`**; set `digest_month` to override the target month.

Empty scrapes fail without replacing the snapshot. Known releases retain their timestamps and details, including when they disappear from the listing. Archived releases remain known for duplicate detection. Badge and run-log totals include active and archived releases, and failure logs and badges are committed even when the tracker fails.

SMTP errors preserve the previous snapshot so new releases remain eligible for notification on the next run. If only some recipients accept a message, the run fails and retries the whole notification next time; recipients who already accepted it may receive a duplicate. SMTP acceptance does not guarantee inbox delivery. A failure after sending but before saving or pushing state can also cause duplicate notifications.

Enrichment failures are saved under each release's `enrichment` field (`status`, `attempts`, `last_attempt_at`, and `error` on failure). Successful details are reused. After three failed attempts, remove that release's `enrichment` field to allow another retry cycle. Backfilling details does not send another release notification; archived releases and PDFs are excluded. The limits are defined by `ENRICHMENT_BATCH_SIZE`, `ENRICHMENT_MAX_ATTEMPTS`, and `ENRICHMENT_RETRY_HOURS` in `scraper.py`.

---

## Running tests locally

```bash
pip install -r requirements.txt
pytest tests -v
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
