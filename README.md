# 🤖 AI Fresher Job Hunter

> **Automated daily fresher job alerts powered by GPT-4o-mini, SerpAPI, RapidAPI, and GitHub Actions.**

Every day at **6:00 AM IST**, this system automatically:

1. Fetches fresh jobs from **Google Jobs**, **JSearch (LinkedIn/Indeed/Glassdoor)**, and **LinkedIn**.
2. Deduplicates across sources and across previous days (SQLite).
3. Pre-filters with regex (rejects senior/lead roles cheaply).
4. Scores & ranks with **GPT-4o-mini** (1–100 relevance score).
5. Generates a **responsive HTML email report** with score badges, apply buttons, and trend charts.
6. Sends the email via **Gmail SMTP**.
7. Optionally sends **Telegram alerts** with the top 5 jobs.
8. Saves all jobs to **CSV** (daily + master) and optionally **Google Sheets**.
9. Runs 100% automatically on **GitHub Actions** — zero infra needed.

---

## Architecture

```
GitHub Actions (cron: 6 AM IST)
          │
          ▼
      main.py
          │
    ┌─────┴──────────────────────┐
    │   Parallel Fetch (threads) │
    │  ┌──────────────────────┐  │
    │  │ fetch_google_jobs.py │  │────→ SerpAPI (Google Jobs engine)
    │  │ fetch_jsearch_jobs.py│  │────→ RapidAPI JSearch
    │  │ fetch_linkedin_posts │  │────→ SerpAPI (Google organic + LinkedIn URLs)
    │  └──────────────────────┘  │
    └─────────────────────────────┘
          │
          ▼
    deduplicator.py     (SQLite 30-day rolling window)
          │
          ▼
    ai_filter.py        (GPT-4o-mini, 20 jobs/batch)
          │
          ├─────────────────────────────────────────────────────┐
          ▼                                                     ▼
   email_generator.py                                   storage.py
   + mailer.py                                     (CSV + Google Sheets)
   (Gmail SMTP)
          │
          ▼
   Telegram alert (optional)
```

---

## Prerequisites

| Tool | Version |
|------|---------|
| Python | 3.11+ |
| pip | Latest |
| Git | Any |
| Docker | 24+ (optional) |

---

## Quick Start (Local)

### 1. Clone the repository

```bash
git clone https://github.com/YOUR_USERNAME/ai-fresher-job-hunter.git
cd ai-fresher-job-hunter
```

### 2. Set up virtual environment

```bash
python -m venv venv
source venv/bin/activate        # macOS/Linux
# venv\Scripts\activate         # Windows
pip install -r requirements.txt
```

### 3. Configure environment variables

```bash
cp .env.example .env
# Edit .env with your API keys (see API Setup below)
nano .env
```

### 4. Run

```bash
python main.py
```

Check `logs/job_hunter.log` and your inbox!

---

## API Setup

### OpenAI API Key
1. Go to [platform.openai.com/api-keys](https://platform.openai.com/api-keys)
2. Create a new secret key
3. Set `OPENAI_API_KEY=sk-...` in `.env`
4. Add $5 credit to your account (estimated cost: ~$0.03/month)

### SerpAPI Key
1. Go to [serpapi.com](https://serpapi.com/manage-api-key)
2. Sign up (free plan = 100 searches/month)
3. Copy your API key
4. Set `SERPAPI_KEY=...` in `.env`

### RapidAPI (JSearch)
1. Go to [rapidapi.com/letscrape-6bRBa3QguO5/api/jsearch](https://rapidapi.com/letscrape-6bRBa3QguO5/api/jsearch)
2. Subscribe to the **Basic** (free) plan: 200 requests/month
3. Copy your RapidAPI key from the dashboard
4. Set `RAPIDAPI_KEY=...` in `.env`

### Gmail App Password
1. Log into your Google account
2. Go to **Security** → **2-Step Verification** (enable it if not already)
3. Scroll down to **App passwords**
4. Create a new app password: App = `Mail`, Device = `Other (AI Job Hunter)`
5. Copy the 16-character password
6. Set `EMAIL_PASS=xxxx xxxx xxxx xxxx` (with or without spaces — both work)

### Telegram Bot (Optional)
1. Open Telegram → search `@BotFather`
2. Send `/newbot` and follow prompts
3. Copy the bot token → set `TELEGRAM_BOT_TOKEN=...`
4. Send a message to your bot, then visit:
   `https://api.telegram.org/bot<TOKEN>/getUpdates`
5. Find `chat.id` in the response → set `TELEGRAM_CHAT_ID=...`

### Google Sheets (Optional)
1. Create a Google Sheet and copy its ID from the URL
2. In [Google Cloud Console](https://console.cloud.google.com/):
   - Create a project → Enable **Google Sheets API** and **Google Drive API**
   - Create a **Service Account** → Download JSON key
3. Share the spreadsheet with the service account email (`xxxxx@xxxxx.iam.gserviceaccount.com`)
4. Encode the JSON: `base64 -i service_account.json | tr -d '\n'`
5. Set `GOOGLE_SHEETS_ID=...` and `GOOGLE_SERVICE_ACCOUNT_JSON=<base64>`

---

## GitHub Actions Deployment (Recommended)

### 1. Create a new GitHub repository

```bash
git init
git remote add origin https://github.com/YOUR_USERNAME/ai-fresher-job-hunter.git
git add .
git commit -m "feat: initial production setup"
git push -u origin main
```

### 2. Add GitHub Secrets

Go to your repository → **Settings** → **Secrets and variables** → **Actions** → **New repository secret**

Add each of the following secrets:

| Secret Name | Example Value |
|-------------|---------------|
| `OPENAI_API_KEY` | `sk-proj-...` |
| `SERPAPI_KEY` | `abc123...` |
| `RAPIDAPI_KEY` | `xyz789...` |
| `EMAIL_USER` | `yourname@gmail.com` |
| `EMAIL_PASS` | `abcd efgh ijkl mnop` |
| `MY_EMAIL` | `recipient@gmail.com` |
| `TELEGRAM_BOT_TOKEN` | `7123456789:AAF...` (optional) |
| `TELEGRAM_CHAT_ID` | `123456789` (optional) |
| `GOOGLE_SHEETS_ID` | `1BxiMV...` (optional) |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | Base64 string (optional) |

### 3. Enable GitHub Actions

Go to **Actions** tab → click **Enable Actions** if prompted.

### 4. Test with manual trigger

Go to **Actions** → **AI Fresher Job Hunter** → **Run workflow** → click **Run workflow**.

Watch the live logs. CSV + log files are uploaded as artifacts.

### 5. Automatic daily schedule

The workflow runs automatically at **00:30 UTC = 6:00 AM IST** every day.

---

## Running with Docker

```bash
# Build and run once
docker-compose run --rm job-hunter

# Build without running
docker-compose build

# View logs from a previous run
docker-compose logs job-hunter

# Check generated CSV files (stored in named volume)
docker volume inspect ai-fresher-job-hunter_job_data
```

---

## Project Structure

```
ai-fresher-job-hunter/
├── .github/
│   └── workflows/
│       └── job_hunter.yml        ← GitHub Actions (6 AM IST daily)
├── config/
│   └── settings.py               ← Pydantic settings + all constants
├── services/
│   ├── fetch_google_jobs.py      ← SerpAPI Google Jobs fetcher
│   ├── fetch_jsearch_jobs.py     ← RapidAPI JSearch fetcher
│   ├── fetch_linkedin_posts.py   ← LinkedIn via SerpAPI organic
│   ├── ai_filter.py              ← GPT-4o-mini scoring engine
│   ├── email_generator.py        ← Jinja2 HTML report builder
│   ├── mailer.py                 ← Gmail SMTP sender
│   ├── deduplicator.py           ← SQLite cross-day dedup
│   ├── logger.py                 ← Rotating file + console logger
│   └── storage.py                ← CSV + Google Sheets writer
├── templates/
│   └── email_template.html       ← Responsive HTML email template
├── utils/
│   └── helpers.py                ← Shared dataclass, retry decorator, utils
├── data/                         ← Generated CSVs (gitignored)
├── logs/                         ← Log files (gitignored)
├── main.py                       ← Main orchestrator
├── requirements.txt
├── .env.example
├── .gitignore
├── Dockerfile
└── docker-compose.yml
```

---

## Cost Estimate

| Service | Usage | Cost/Month |
|---------|-------|-----------|
| OpenAI GPT-4o-mini | ~4 calls/day × 1200 tokens × 30 | **~$0.03** |
| SerpAPI | ~24 calls/day (8 queries × 3 engines) × 30 ≈ 720/month | **~$50** (Hobby plan) or **free** (100/month limit) |
| RapidAPI JSearch | ~8 calls/day × 30 = 240/month | **Free** (200/month limit) or **$10** (paid) |
| GitHub Actions | ~5 min/day | **Free** (2000 min/month included) |
| Gmail SMTP | 1 email/day | **Free** |

> **Cost-optimisation tip:** Use SerpAPI's free 100 searches/month for testing.
> For production, reduce queries per run or upgrade to the Hobby plan ($50/month).
> JSearch free tier (200/month) covers 6-7 queries/day comfortably.

---

## Customising the System

### Change target roles or skills
Edit `config/settings.py`:
```python
CANDIDATE = {
    "name": "Your Name",
    "target_roles": ["Java Developer", "React Developer"],
    "skills": { ... },
}
```

### Change search queries
Edit `SEARCH_QUERIES` list in `config/settings.py`.

### Change AI score threshold
Set `MIN_AI_SCORE=60` in `.env` (higher = stricter filtering).

### Change email job count
Set `TOP_JOBS_IN_EMAIL=10` in `.env`.

### Change schedule
Edit the cron in `.github/workflows/job_hunter.yml`:
```yaml
- cron: "30 0 * * *"   # change to your desired UTC time
```

---

## Troubleshooting

### ❌ `ValidationError: OPENAI_API_KEY field required`
→ Check that your `.env` file exists and has the correct variable names.
→ For GitHub Actions, verify all secrets are added under Settings → Secrets.

### ❌ `SMTPAuthenticationError`
→ You're using your Gmail login password instead of an **App Password**.
→ Follow the Gmail App Password setup steps above.
→ Ensure 2-Step Verification is enabled on your Google account.

### ❌ `SerpAPI error: Invalid API key`
→ Check `SERPAPI_KEY` value. Copy it fresh from [serpapi.com/manage-api-key](https://serpapi.com/manage-api-key).

### ❌ `RapidAPI 403 / You are not subscribed`
→ You need to subscribe to JSearch on RapidAPI (free plan available).
→ Visit the JSearch API page and click **Subscribe to Test**.

### ❌ No jobs in the email
→ Check `logs/job_hunter.log` for which step produced 0 results.
→ Run with `LOG_LEVEL=DEBUG` for verbose output.
→ The dedup DB may have marked all today's jobs as already seen — delete `data/seen_jobs.db` to reset.

### ❌ `ModuleNotFoundError`
→ Activate your virtual environment: `source venv/bin/activate`
→ Reinstall: `pip install -r requirements.txt`

### ❌ GitHub Actions timing is wrong
→ Remember: cron is in **UTC**. `30 0 * * *` = 00:30 UTC = 06:00 IST.
→ GitHub Actions may delay by up to ~15 minutes during high load.

---

## Security Notes

- **Never commit `.env`** — it's in `.gitignore`.
- **Use GitHub Secrets** for all API keys in CI/CD.
- The Docker container runs as a **non-root user** (UID 1001).
- Gmail App Passwords are scoped only to one app — revoke at any time in Google Security settings.
- All API calls use HTTPS.
- Job descriptions are truncated before being sent to OpenAI to minimise data exposure.

---

## License

MIT License — free to use, modify, and distribute.

---

*Built with Python 3.11 · OpenAI GPT-4o-mini · SerpAPI · RapidAPI · GitHub Actions*
