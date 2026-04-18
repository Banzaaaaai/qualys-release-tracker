# Qualys Release Tracker

Daily tracker that monitors the [Qualys Suite Release Notes](https://www.qualys.com/documentation/release-notes) page and sends an HTML email notification whenever new releases are published.

Runs automatically every day at **07:00 UTC** via GitHub Actions.

---

## How it works

1. **Scrapes** `qualys.com/documentation/release-notes` and parses every release entry (title, URL, module tags).
2. **Diffs** against `snapshot.json` (committed in this repo) to identify new entries.
3. **Emails** a formatted HTML report listing new releases with module badges and priority tiers.
4. **Commits** the updated snapshot back to the repo so the next run has an accurate baseline.

---

## Setup

### 1. Fork / clone this repo to your GitHub account

### 2. Add the following GitHub Actions secrets

Go to **Settings → Secrets and variables → Actions → New repository secret**:

| Secret name     | Value                                             |
|-----------------|---------------------------------------------------|
| `SMTP_HOST`     | e.g. `smtp.gmail.com`                             |
| `SMTP_PORT`     | `587`                                             |
| `SMTP_USER`     | Your sending email address                        |
| `SMTP_PASSWORD` | App password (Gmail) or SMTP token                |
| `EMAIL_TO`      | Recipient(s), comma-separated                     |

#### Gmail app password
1. Enable 2FA on your Google account.
2. Go to **Google Account → Security → App passwords**.
3. Create a password for "Mail / Other".
4. Use that 16-character password as `SMTP_PASSWORD`.

### 3. Enable Actions on the repository

Go to **Actions** tab → click **Enable Actions** if prompted.

### 4. Test the workflow manually

Go to **Actions → Qualys Release Tracker → Run workflow**.  
Toggle `force_notify = true` on the first run to verify the email arrives.

---

## Priority tiers

| Tier       | Module tags                              |
|------------|------------------------------------------|
| 🔴 HIGH    | `VM` `VMDR` `PC` `API` `VMDR OT`        |
| 🟡 MEDIUM  | `CA` `ETM` `CSAM` `GAV` `PM` `EDR` `FIM`|
| 🔵 OTHER   | Everything else                          |

---

## Files

| File                                  | Purpose                          |
|---------------------------------------|----------------------------------|
| `scraper.py`                          | Main scraper + diff + email logic|
| `snapshot.json`                       | Last-known state (auto-updated)  |
| `requirements.txt`                    | Python dependencies              |
| `.github/workflows/tracker.yml`       | GitHub Actions schedule          |

---

## Local testing

```bash
pip install -r requirements.txt

export SMTP_HOST=smtp.gmail.com
export SMTP_PORT=587
export SMTP_USER=you@gmail.com
export SMTP_PASSWORD=yourapppassword
export EMAIL_TO=you@gmail.com

python scraper.py
```

Delete `snapshot.json` before the first local run to treat all current entries as new (useful for a full email test).
