"""
services/fetch_linkedin_posts.py
─────────────────────────────────
Fetches EXCLUSIVE LinkedIn POSTS (recruiter & HR activity posts) via SerpAPI
Google search: site:linkedin.com/posts

WHY POSTS (not job listings):
  - linkedin.com/jobs/view/ pages are generic job board listings.
  - linkedin.com/posts/ are direct recruiter/HR posts announcing openings —
    these are more exclusive, fresher, and harder to find on job boards.
  - Google indexes these public posts; SerpAPI retrieves them legally.

WHAT WE GET:
  Actual recruiter posts saying "We are hiring!", "Job opening for freshers",
  with the real LinkedIn post URL, poster name, and job description snippet.
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

# Hiring-intent queries targeting actual LinkedIn posts — not job listing pages
_LI_POST_QUERIES = [
    '"hiring" "fresher" "Java" (Bangalore OR Bengaluru OR Remote OR India) developer',
    '"hiring" "fresher" "React" (Bangalore OR Bengaluru OR Remote OR India) developer',
    '"hiring" "fresher" "Python" (Bangalore OR Bengaluru OR Remote OR India) developer',
    '"we are hiring" "fresher" "software engineer" (Bangalore OR Bengaluru OR Remote OR India)',
    '"job opening" "fresher" "full stack" (Bangalore OR Bengaluru OR Remote OR India)',
    '"looking for" "fresher" developer (Bangalore OR Bengaluru OR India) 2025 OR 2026',
    '"entry level" "hiring" (Bangalore OR Bengaluru OR India) developer engineer',
    '"0-1 year" OR "0-2 years" hiring (Bangalore OR Bengaluru OR India) developer',
]


class LinkedInFetcher:
    """Fetches exclusive LinkedIn recruiter posts via SerpAPI Google search."""

    def __init__(self) -> None:
        settings = get_settings()
        self._api_key = settings.serpapi_key
        self._max_results = 10  # organic results per query

    # ── Public API ────────────────────────────────────────────────────────

    def fetch_all(self) -> List[Job]:
        all_jobs: List[Job] = []
        seen_urls: set[str] = set()

        for query in _LI_POST_QUERIES:
            try:
                jobs = self._fetch_query(query)
                for job in jobs:
                    if job.url and job.url not in seen_urls:
                        seen_urls.add(job.url)
                        all_jobs.append(job)
                log.info("[LinkedIn Posts] Query '%s' → %d results", query[:50], len(jobs))
            except Exception as exc:
                log.error("[LinkedIn Posts] Query '%s' failed: %s", query[:50], exc)
            finally:
                time.sleep(_SLEEP_BETWEEN_QUERIES)

        log.info("[LinkedIn Posts] Total fetched: %d posts", len(all_jobs))
        return all_jobs

    # ── Private Methods ───────────────────────────────────────────────────

    @retry_with_backoff(max_attempts=_MAX_RETRIES, exceptions=(requests.RequestException,))
    def _fetch_query(self, query: str) -> List[Job]:
        """Search Google for LinkedIn post URLs matching the hiring query."""
        search_query = f"site:linkedin.com/posts {query}"
        params = {
            "engine": "google",
            "q": search_query,
            "hl": "en",
            "gl": "in",
            "num": self._max_results,
            "tbs": "qdr:w",        # last 7 days only — no old posts
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
            job = self._parse_post_result(r)
            if job:
                jobs.append(job)
        return jobs

    def _parse_post_result(self, raw: dict) -> Job | None:
        """Convert a Google organic result pointing to a LinkedIn post into a Job."""
        url = safe_get(raw, "link", default="")
        if "linkedin.com/posts" not in url:
            return None

        # Raw title from Google: "John Doe on LinkedIn: 'We are hiring Java freshers...'"
        raw_title = clean_text(safe_get(raw, "title", default=""))
        snippet = clean_text(safe_get(raw, "snippet", default=""))

        # Extract poster name (before " on LinkedIn")
        poster = ""
        poster_match = re.match(r"^(.+?)\s+on\s+LinkedIn", raw_title, re.IGNORECASE)
        if poster_match:
            poster = poster_match.group(1).strip()

        # Extract the actual post content (after the colon in the title)
        post_text = raw_title
        colon_match = re.search(r'on LinkedIn[:\s]+["\'\u201c\u2018]?(.+)', raw_title, re.IGNORECASE)
        if colon_match:
            post_text = colon_match.group(1).strip().strip('"\'\\u201c\\u201d\\u2018\\u2019')

        # Derive a job title from the post content
        title = _extract_job_title(post_text, snippet)
        if not title:
            title = post_text[:80] if post_text else "LinkedIn Job Post"

        # Company: try to extract from "at <Company>" in post/snippet
        company = poster  # fallback to poster name (usually recruiter/HR)
        at_match = re.search(r"\bat\s+([A-Z][^,.\n]{2,40})(?=[,.\n]|$)", post_text + " " + snippet)
        if at_match:
            company = at_match.group(1).strip()

        # Location: look for Indian cities or "India"
        location = _extract_location(post_text + " " + snippet)

        description = f"{post_text}\n\n{snippet}".strip()

        return Job(
            title=title,
            company=clean_text(company),
            location=location,
            url=url,
            description=truncate_text(description, 800),
            source="linkedin_posts",
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
    re.compile(r"(react\s+(?:developer|engineer|js))", re.IGNORECASE),
    re.compile(r"((?:frontend|front-end|front\s+end)\s+developer)", re.IGNORECASE),
    re.compile(r"((?:backend|back-end|back\s+end)\s+developer)", re.IGNORECASE),
    re.compile(r"(full\s+stack\s+developer)", re.IGNORECASE),
    re.compile(r"(software\s+engineer)", re.IGNORECASE),
    re.compile(r"(python\s+developer)", re.IGNORECASE),
    re.compile(r"(spring\s+boot\s+developer)", re.IGNORECASE),
    re.compile(r"(associate\s+software\s+engineer)", re.IGNORECASE),
    re.compile(r"(web\s+developer)", re.IGNORECASE),
]


def _extract_job_title(post_text: str, snippet: str) -> str:
    combined = post_text + " " + snippet
    for pattern in _TITLE_PATTERNS:
        m = pattern.search(combined)
        if m:
            return m.group(1).strip().title()
    return ""


def _extract_location(text: str) -> str:
    m = _INDIA_CITIES.search(text)
    return m.group(0).strip() if m else "India"

