"""
services/fetch_google_jobs.py
─────────────────────────────
Fetches fresher jobs from Google Jobs via SerpAPI.

API docs: https://serpapi.com/google-jobs-api
Rate limit: depends on plan (free = 100 searches/month)

Each of the 8 canonical search queries is executed in sequence with
a 1-second sleep between calls to respect rate limits.  Results are
normalised into the shared Job dataclass.
"""

from __future__ import annotations

import time
from typing import List

import requests

from config.settings import SEARCH_QUERIES, get_settings
from services.logger import get_logger
from utils.helpers import Job, clean_text, normalise_salary, retry_with_backoff, safe_get, truncate_text

log = get_logger(__name__)

_API_ENDPOINT = "https://serpapi.com/search"
_SLEEP_BETWEEN_QUERIES = 1.2   # seconds — stay well under rate limit
_MAX_RETRIES = 3


class GoogleJobsFetcher:
    """Fetches jobs from Google Jobs search results via SerpAPI."""

    def __init__(self) -> None:
        settings = get_settings()
        self._api_key = settings.serpapi_key
        self._max_results = settings.max_jobs_per_source

    # ── Public API ────────────────────────────────────────────────────────

    def fetch_all(self) -> List[Job]:
        """
        Run all search queries and return a deduplicated list of Job objects.
        """
        all_jobs: List[Job] = []
        seen_urls: set[str] = set()

        for query in SEARCH_QUERIES:
            try:
                jobs = self._fetch_query(query)
                for job in jobs:
                    if job.url and job.url not in seen_urls:
                        seen_urls.add(job.url)
                        all_jobs.append(job)
                log.info("[Google Jobs] Query '%s' → %d results", query, len(jobs))
            except Exception as exc:
                log.error("[Google Jobs] Query '%s' failed: %s", query, exc)
            finally:
                time.sleep(_SLEEP_BETWEEN_QUERIES)

        log.info("[Google Jobs] Total fetched: %d jobs", len(all_jobs))
        return all_jobs[: self._max_results * len(SEARCH_QUERIES)]

    # ── Private Methods ───────────────────────────────────────────────────

    @retry_with_backoff(max_attempts=_MAX_RETRIES, exceptions=(requests.RequestException,))
    def _fetch_query(self, query: str) -> List[Job]:
        """Call SerpAPI google_jobs engine for a single query."""
        params = {
            "engine": "google_jobs",
            "q": query,
            "hl": "en",
            "gl": "in",          # India
            "num": self._max_results,
            "api_key": self._api_key,
        }
        response = requests.get(_API_ENDPOINT, params=params, timeout=20)
        response.raise_for_status()
        data = response.json()

        if "error" in data:
            raise RuntimeError(f"SerpAPI error: {data['error']}")

        return [self._parse_result(r) for r in data.get("jobs_results", [])]

    def _parse_result(self, raw: dict) -> Job:
        """Convert a raw SerpAPI job result into a Job object."""
        # Build the best available apply URL
        apply_options = raw.get("apply_options", [])
        url = ""
        if apply_options:
            url = apply_options[0].get("link", "")
        if not url:
            url = safe_get(raw, "related_links", 0, "link", default="")

        # Detected extensions (salary, job type, posted date)
        extensions = raw.get("detected_extensions", {})
        highlights = raw.get("job_highlights", [])
        description_parts = [safe_get(h, "title") + ": " + ", ".join(safe_get(h, "items", default=[])) for h in highlights]
        description = clean_text(raw.get("description", "") or " ".join(description_parts))

        return Job(
            title=clean_text(raw.get("title", "")),
            company=clean_text(raw.get("company_name", "")),
            location=clean_text(raw.get("location", "India")),
            url=url,
            description=truncate_text(description, 800),
            source="google_jobs",
            posted_date=str(extensions.get("posted_at", "")),
            salary=normalise_salary(str(extensions.get("salary", ""))),
            job_type=clean_text(str(extensions.get("work_from_home", "")).replace("True", "Remote")),
        )
