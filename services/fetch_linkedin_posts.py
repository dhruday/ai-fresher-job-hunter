"""
services/fetch_linkedin_posts.py
─────────────────────────────────
Fetches LinkedIn job posts using SerpAPI's organic Google search with
site:linkedin.com/jobs filters.

WHY THIS APPROACH:
  - Direct LinkedIn scraping violates LinkedIn ToS.
  - LinkedIn's official API requires partner approval (closed).
  - Google indexes LinkedIn job URLs publicly.
  - SerpAPI provides a legal, production-safe way to retrieve them.

WHAT WE GET:
  Title, company, location, and description snippet from Google's cached
  result of the LinkedIn job post page — no direct scraping involved.
"""

from __future__ import annotations

import re
import time
from typing import List

import requests

from config.settings import SEARCH_QUERIES, get_settings
from services.logger import get_logger
from utils.helpers import Job, clean_text, normalise_salary, retry_with_backoff, safe_get, truncate_text

log = get_logger(__name__)

_API_ENDPOINT = "https://serpapi.com/search"
_SLEEP_BETWEEN_QUERIES = 1.2
_MAX_RETRIES = 3
_LINKEDIN_URL_RE = re.compile(r"https?://(?:www\.)?linkedin\.com/jobs/view/\S+", re.IGNORECASE)


class LinkedInFetcher:
    """Fetches LinkedIn jobs via SerpAPI organic search (Google-indexed results)."""

    def __init__(self) -> None:
        settings = get_settings()
        self._api_key = settings.serpapi_key
        self._max_results = min(settings.max_jobs_per_source, 10)  # organic results are fewer

    # ── Public API ────────────────────────────────────────────────────────

    def fetch_all(self) -> List[Job]:
        """Run all search queries against LinkedIn job URLs and return Job objects."""
        all_jobs: List[Job] = []
        seen_urls: set[str] = set()

        for query in SEARCH_QUERIES:
            try:
                jobs = self._fetch_query(query)
                for job in jobs:
                    if job.url and job.url not in seen_urls:
                        seen_urls.add(job.url)
                        all_jobs.append(job)
                log.info("[LinkedIn] Query '%s' → %d results", query, len(jobs))
            except Exception as exc:
                log.error("[LinkedIn] Query '%s' failed: %s", query, exc)
            finally:
                time.sleep(_SLEEP_BETWEEN_QUERIES)

        log.info("[LinkedIn] Total fetched: %d jobs", len(all_jobs))
        return all_jobs

    # ── Private Methods ───────────────────────────────────────────────────

    @retry_with_backoff(max_attempts=_MAX_RETRIES, exceptions=(requests.RequestException,))
    def _fetch_query(self, query: str) -> List[Job]:
        """Query Google for LinkedIn job URLs matching the search term."""
        search_query = f'site:linkedin.com/jobs "{query}" (fresher OR "entry level" OR "0-2 years") India'
        params = {
            "engine": "google",
            "q": search_query,
            "hl": "en",
            "gl": "in",
            "num": self._max_results,
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
            job = self._parse_organic_result(r)
            if job:
                jobs.append(job)
        return jobs

    def _parse_organic_result(self, raw: dict) -> Job | None:
        """
        Convert a Google organic search result (pointing to a LinkedIn job)
        into a Job object.  Returns None if the URL doesn't look like a job.
        """
        url = safe_get(raw, "link", default="")
        if "linkedin.com/jobs" not in url:
            return None

        title = clean_text(safe_get(raw, "title", default=""))
        # LinkedIn organic titles often look like: "Software Engineer at Acme | LinkedIn"
        # Clean that up:
        title = re.sub(r"\s*[\|–]\s*LinkedIn.*$", "", title, flags=re.IGNORECASE).strip()

        snippet = clean_text(safe_get(raw, "snippet", default=""))

        # Try to extract company + location from rich snippet / snippet
        company = ""
        location = "India"
        rich = raw.get("rich_snippet", {})
        top = rich.get("top", {})
        detected_ext = top.get("extensions", [])

        # Heuristic: first extension is usually the company, second is location
        if len(detected_ext) >= 2:
            company = detected_ext[0]
            location = detected_ext[1]
        elif len(detected_ext) == 1:
            company = detected_ext[0]

        # Fallback: parse company from title " at Company"
        if not company:
            at_match = re.search(r"\bat\s+(.+?)(?:\s*[-–|]|$)", title, re.IGNORECASE)
            if at_match:
                company = at_match.group(1).strip()

        return Job(
            title=title,
            company=clean_text(company),
            location=clean_text(location),
            url=url,
            description=truncate_text(snippet, 800),
            source="linkedin",
            posted_date="",
            salary=normalise_salary(""),
            job_type="",
        )
