"""
main.py
────────
AI Fresher Job Hunter — Orchestrator

Execution flow:
  1. Parallel job fetching  (Google Jobs + JSearch + LinkedIn)
  2. Merge & deduplicate    (within-run + cross-day SQLite)
  3. AI filter & score      (GPT-4o-mini, batched)
  4. Generate HTML report   (Jinja2 email template)
  5. Send email             (Gmail SMTP SSL)
  6. Send Telegram alert    (optional)
  7. Persist to CSV         (daily + master)
  8. Persist to Sheets      (optional)
  9. Log run summary

Run locally:
    python main.py

Run via Docker:
    docker-compose up

Scheduled via GitHub Actions (6 AM IST daily = 00:30 UTC cron).
"""

from __future__ import annotations

import asyncio
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from typing import List

# ── Bootstrap .env ─────────────────────────────────────────────────────────
from dotenv import load_dotenv
load_dotenv(override=False)   # reads .env, does NOT override real env vars

# ── Internal imports ───────────────────────────────────────────────────────
from config.settings import get_settings
from services.ai_filter import AIFilter
from services.deduplicator import Deduplicator
from services.email_generator import EmailGenerator
from services.fetch_google_jobs import GoogleJobsFetcher
from services.fetch_jsearch_jobs import JSearchFetcher
from services.fetch_linkedin_posts import LinkedInFetcher
from services.fetch_twitter_posts import TwitterFetcher
from services.logger import get_logger
from services.mailer import Mailer
from services.storage import Storage
from utils.helpers import Job

log = get_logger(__name__)


# ── Telegram Alert ─────────────────────────────────────────────────────────

def send_telegram_alert(jobs: List[Job], settings) -> None:
    """
    Send a formatted Telegram message with the top 5 jobs.
    Does nothing if TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID is unset.
    """
    if not settings.telegram_bot_token or not settings.telegram_chat_id:
        log.debug("Telegram not configured — skipping")
        return

    import requests  # local import to keep startup fast

    top5 = jobs[:5]
    lines = [
        f"🤖 *AI Job Report — {date.today().strftime('%d %b %Y')}*",
        f"✅ {len(jobs)} fresher jobs found today!\n",
    ]
    for i, job in enumerate(top5, 1):
        emoji = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else "▪️"
        lines.append(
            f"{emoji} *{job.title}* @ {job.company}\n"
            f"   📍 {job.location}  |  Score: {job.score}/100\n"
            f"   [Apply →]({job.url})\n"
        )
    message = "\n".join(lines)

    url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage"
    payload = {
        "chat_id": settings.telegram_chat_id,
        "text": message,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True,
    }
    try:
        resp = requests.post(url, json=payload, timeout=15)
        if resp.ok:
            log.info("Telegram alert sent (top %d jobs)", len(top5))
        else:
            log.warning("Telegram API error: %s — %s", resp.status_code, resp.text[:200])
    except Exception as exc:
        log.warning("Telegram send failed (non-fatal): %s", exc)


# ── Parallel Fetching ──────────────────────────────────────────────────────

def fetch_all_sources() -> List[Job]:
    """
    Run all three fetchers concurrently using a thread pool.
    Each fetcher is I/O-bound (API calls), so threading is appropriate.
    Returns merged list of all raw jobs.
    """
    fetchers = {
        "Google Jobs": GoogleJobsFetcher(),
        "JSearch": JSearchFetcher(),
        "LinkedIn Posts": LinkedInFetcher(),
        "Twitter Posts": TwitterFetcher(),
    }

    all_jobs: List[Job] = []

    with ThreadPoolExecutor(max_workers=4) as executor:
        future_to_name = {
            executor.submit(fetcher.fetch_all): name
            for name, fetcher in fetchers.items()
        }
        for future in as_completed(future_to_name):
            name = future_to_name[future]
            try:
                jobs = future.result()
                log.info("Fetcher '%s' returned %d jobs", name, len(jobs))
                all_jobs.extend(jobs)
            except Exception as exc:
                log.error("Fetcher '%s' crashed: %s", name, exc)

    log.info("Total raw jobs fetched: %d", len(all_jobs))
    return all_jobs


# ── Main Orchestrator ──────────────────────────────────────────────────────

def run() -> int:
    """
    Main execution pipeline.
    Returns Unix exit code: 0 = success, 1 = failure.
    """
    start_time = time.monotonic()
    settings = get_settings()

    log.info("=" * 60)
    log.info("AI Fresher Job Hunter — START  (%s)", date.today().isoformat())
    log.info("=" * 60)

    # ── Step 1: Fetch ──────────────────────────────────────────────────
    log.info("STEP 1/7 — Fetching jobs from all sources …")
    try:
        raw_jobs = fetch_all_sources()
    except Exception as exc:
        log.critical("Fetching failed catastrophically: %s", exc)
        return 1

    if not raw_jobs:
        log.warning("No jobs fetched from any source — aborting run")
        return 0

    # ── Step 2: Deduplicate ────────────────────────────────────────────
    log.info("STEP 2/7 — Deduplicating %d jobs …", len(raw_jobs))
    dedup = Deduplicator()
    try:
        new_jobs = dedup.filter_new(raw_jobs)
    except Exception as exc:
        log.error("Deduplication failed: %s — using all raw jobs", exc)
        new_jobs = raw_jobs
    finally:
        pass   # keep dedup open for mark_seen below

    if not new_jobs:
        log.info("No new jobs today (all already seen) — sending empty report")
        # Still send a "nothing new" email so user knows the system ran
        html = EmailGenerator().generate([])
        Mailer().send(html, job_count=0)
        dedup.close()
        return 0

    # ── Step 3: AI Filter & Score ──────────────────────────────────────
    log.info("STEP 3/7 — AI scoring %d new jobs …", len(new_jobs))
    try:
        ai = AIFilter()
        scored_jobs = ai.filter_and_score(new_jobs)
    except Exception as exc:
        log.error("AI filtering failed: %s — using pre-filter results with default scores", exc)
        scored_jobs = new_jobs
        for job in scored_jobs:
            if job.score == 0:
                job.score = 50

    if not scored_jobs:
        log.warning("All jobs rejected by AI filter — no report generated")
        dedup.close()
        return 0

    log.info(
        "Scoring complete: top score=%d, avg_score=%d, total=%d",
        scored_jobs[0].score,
        round(sum(j.score for j in scored_jobs) / len(scored_jobs)),
        len(scored_jobs),
    )

    # ── Step 4: Generate HTML Email ────────────────────────────────────
    log.info("STEP 4/7 — Generating HTML report …")
    try:
        html = EmailGenerator().generate(scored_jobs)
    except Exception as exc:
        log.error("Email generation failed: %s", exc)
        dedup.close()
        return 1

    # ── Step 5: Send Email ─────────────────────────────────────────────
    log.info("STEP 5/7 — Sending email to %s …", settings.my_email)
    email_sent = Mailer().send(html, job_count=len(scored_jobs))
    if not email_sent:
        log.error("Email delivery failed — continuing to save jobs locally")

    # ── Step 6: Telegram Alert ─────────────────────────────────────────
    log.info("STEP 6/7 — Sending Telegram alert …")
    send_telegram_alert(scored_jobs, settings)

    # ── Step 7: Store Jobs ─────────────────────────────────────────────
    log.info("STEP 7/7 — Saving jobs to CSV (and optionally Sheets) …")
    try:
        daily_csv = Storage().save(scored_jobs)
        log.info("Jobs saved to: %s", daily_csv)
    except Exception as exc:
        log.error("Storage failed (non-fatal): %s", exc)

    # Mark all new jobs as seen AFTER successful processing
    try:
        dedup.mark_seen(scored_jobs)
    except Exception as exc:
        log.error("Failed to mark jobs as seen: %s", exc)
    finally:
        dedup.close()

    elapsed = time.monotonic() - start_time
    log.info("=" * 60)
    log.info(
        "RUN COMPLETE in %.1fs | %d jobs fetched | %d new | %d passed AI | email=%s",
        elapsed,
        len(raw_jobs),
        len(new_jobs),
        len(scored_jobs),
        "sent" if email_sent else "FAILED (check Gmail App Password)",
    )
    log.info("=" * 60)
    # Exit 0 even if email failed — jobs are saved to CSV (email is non-critical)
    return 0


if __name__ == "__main__":
    sys.exit(run())
