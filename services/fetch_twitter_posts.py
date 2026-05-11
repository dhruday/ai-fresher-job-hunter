"""
services/fetch_twitter_posts.py
────────────────────────────────
Fetches exclusive Twitter/X posts from recruiters & HRs announcing fresher
job openings — via SerpAPI Google search: site:x.com

WHY TWITTER POSTS:
  - Recruiters & HRs post job openings on Twitter/X daily
  - These are highly exclusive — not on job boards, direct from source
  - No Twitter API key needed — uses existing SERPAPI_KEY via Google indexing
  - Finds real, actionable leads before they appear on job portals

WHAT WE GET:
  Actual tweets: "We are hiring Java freshers! DM/Apply …"
  with the real tweet URL, poster handle, and full tweet text.
"""

from __future__ import annotations

import re
import time
from typing import List

import requests

from config.settings import get_settings
from services.logger import get_logger
from utils.helpers import Job, clean_text, normalise_salary, retry_with_backoff, safe_get, truncate_text

log = get_logger(__name__)

_API_ENDPOINT = "https://serpapi.com/search"
_SLEEP_BETWEEN_QUERIES = 1.2
_MAX_RETRIES = 3

# Twitter/X recruiter post queries — targeting hiring intent
_TW_QUERIES = [
    '"hiring" "fresher" "Java" developer (Bangalore OR Bengaluru OR Remote OR India)',
    '"hiring" "fresher" "React" developer (Bangalore OR Bengaluru OR Remote OR India)',
    '"hiring" "fresher" "Python" developer (Bangalore OR Bengaluru OR Remote OR India)',
    '"we are hiring" "fresher" software engineer (Bangalore OR Bengaluru OR Remote OR India)',
    '"job opening" "fresher" developer (Bangalore OR Bengaluru OR Remote OR India)',
    '"entry level" hiring developer (Bangalore OR Bengaluru OR India) 2025 OR 2026',
    '"fresher" "full stack" hiring (Bangalore OR Bengaluru OR India)',
]


class TwitterFetcher:
    """Fetches exclusive Twitter/X recruiter posts via SerpAPI Google search."""

    def __init__(self) -> None:
        settings = get_settings()
        self._api_key = settings.serpapi_key
        self._max_results = 10

    # ── Public API ────────────────────────────────────────────────────────

    def fetch_all(self) -> List[Job]:
        all_jobs: List[Job] = []
        seen_urls: set[str] = set()

        for query in _TW_QUERIES:
            try:
                jobs = self._fetch_query(query)
                for job in jobs:
                    if job.url and job.url not in seen_urls:
                        seen_urls.add(job.url)
                        all_jobs.append(job)
                log.info("[Twitter] Query '%s' → %d results", query[:50], len(jobs))
            except Exception as exc:
                log.error("[Twitter] Query '%s' failed: %s", query[:50], exc)
            finally:
                time.sleep(_SLEEP_BETWEEN_QUERIES)

        log.info("[Twitter] Total fetched: %d posts", len(all_jobs))
        return all_jobs

    # ── Private Methods ───────────────────────────────────────────────────

    @retry_with_backoff(max_attempts=_MAX_RETRIES, exceptions=(requests.RequestException,))
    def _fetch_query(self, query: str) -> List[Job]:
        """Search Google for Twitter/X posts matching the hiring query."""
        # Search both x.com and twitter.com (Google indexes both)
        search_query = f"(site:x.com OR site:twitter.com) {query}"
        params = {
            "engine": "google",
            "q": search_query,
            "hl": "en",
            "gl": "in",
            "num": self._max_results,
            "tbs": "qdr:w",        # last 7 days only — no old tweets
            "api_key": self._api_key,
        }
        response = requests.get(_API_ENDPOINT, params=params, timeout=20)
        response.raise_for_status()
        data = response.json()

        if "error" in data:
            raise RuntimeError(f"SerpAPI error: {data['error']}")

        results = data.get("organic_results", [])
        jobs = []
        for r in results:
            job = self._parse_tweet_result(r)
            if job:
                jobs.append(job)
        return jobs

    def _parse_tweet_result(self, raw: dict) -> Job | None:
        """Convert a Google organic result pointing to a tweet into a Job."""
        url = safe_get(raw, "link", default="")
        # Only accept actual tweet status URLs (not profile pages, search pages, etc.)
        if not re.search(r"(x\.com|twitter\.com)/.+/status/\d+", url):
            return None

        raw_title = clean_text(safe_get(raw, "title", default=""))
        snippet = clean_text(safe_get(raw, "snippet", default=""))

        # Twitter organic title format: "@handle: Tweet text... | Twitter" or "Name on Twitter: ..."
        # Extract the handle
        handle = ""
        handle_match = re.match(r"@?(\w+)\s*(?:on Twitter|:)", raw_title, re.IGNORECASE)
        if handle_match:
            handle = f"@{handle_match.group(1)}"

        # Extract tweet text
        tweet_text = raw_title
        tweet_match = re.search(r'(?:on Twitter|on X)[:\s]+["\'\u201c\u2018]?(.+)', raw_title, re.IGNORECASE)
        if tweet_match:
            tweet_text = tweet_match.group(1).strip().strip('"\'\\u201c\\u201d\\u2018\\u2019')
        # Also use snippet as it usually has richer tweet content
        full_text = f"{tweet_text} {snippet}".strip()

        # Derive a job title from the tweet text
        title = _extract_job_title(full_text)
        if not title:
            # Use condensed tweet text as title
            title = tweet_text[:80].rstrip(",. ") if tweet_text else "Twitter Job Post"

        # Company: try to extract from "at <Company>" in tweet, fall back to handle
        company = handle
        at_match = re.search(r"\bat\s+([A-Z][^,.\n]{2,40})(?=[,.\n]|$)", full_text)
        if at_match:
            company = at_match.group(1).strip()

        location = _extract_location(full_text)

        return Job(
            title=title,
            company=clean_text(company),
            location=location,
            url=url,
            description=truncate_text(full_text, 800),
            source="twitter",
            posted_date="",
            salary=normalise_salary(""),
            job_type="",
        )


# ── Helpers ───────────────────────────────────────────────────────────────

_INDIA_CITIES = re.compile(
    r"\b(Bangalore|Bengaluru|Hyderabad|Chennai|Mumbai|Pune|Delhi|Noida|"
    r"Gurgaon|Gurugram|Kolkata|Ahmedabad|India|Remote|Hybrid)\b",
    re.IGNORECASE,
)

_TITLE_PATTERNS = [
    re.compile(r"(java\s+(?:full\s+stack|developer|engineer))", re.IGNORECASE),
    re.compile(r"(react\s+(?:developer|engineer|js\s+developer))", re.IGNORECASE),
    re.compile(r"((?:frontend|front-end)\s+developer)", re.IGNORECASE),
    re.compile(r"((?:backend|back-end)\s+developer)", re.IGNORECASE),
    re.compile(r"(full\s+stack\s+developer)", re.IGNORECASE),
    re.compile(r"(software\s+engineer)", re.IGNORECASE),
    re.compile(r"(python\s+developer)", re.IGNORECASE),
    re.compile(r"(spring\s+boot\s+developer)", re.IGNORECASE),
    re.compile(r"(associate\s+software\s+engineer)", re.IGNORECASE),
    re.compile(r"(web\s+developer)", re.IGNORECASE),
]


def _extract_job_title(text: str) -> str:
    for pattern in _TITLE_PATTERNS:
        m = pattern.search(text)
        if m:
            return m.group(1).strip().title()
    return ""


def _extract_location(text: str) -> str:
    m = _INDIA_CITIES.search(text)
    return m.group(0).strip() if m else "India"
