"""
services/fetch_jsearch_jobs.py
───────────────────────────────
Fetches fresher jobs from JSearch API via RapidAPI.

API docs: https://rapidapi.com/letscrape-6bRBa3QguO5/api/jsearch
Free tier: 200 requests/month
Paid: from $10/month for 5000 requests

JSearch aggregates jobs from LinkedIn, Indeed, Glassdoor, ZipRecruiter
and other boards — high quality, real data, production-safe.
"""

from __future__ import annotations

import time
from typing import List

import requests

from config.settings import SEARCH_QUERIES, get_settings
from services.logger import get_logger
from utils.helpers import Job, clean_text, normalise_salary, retry_with_backoff, safe_get, truncate_text

log = get_logger(__name__)

_API_HOST = "jsearch.p.rapidapi.com"
_API_BASE = f"https://{_API_HOST}"
_SLEEP_BETWEEN_QUERIES = 1.5   # seconds
_MAX_RETRIES = 3


class JSearchFetcher:
    """Fetches jobs from JSearch (RapidAPI) — aggregates LinkedIn, Indeed, etc."""

    def __init__(self) -> None:
        settings = get_settings()
        self._headers = {
            "x-rapidapi-key": settings.rapidapi_key,
            "x-rapidapi-host": _API_HOST,
        }
        self._max_results = settings.max_jobs_per_source

    # ── Public API ────────────────────────────────────────────────────────

    def fetch_all(self) -> List[Job]:
        """Run all search queries and return a combined list of Job objects."""
        all_jobs: List[Job] = []
        seen_ids: set[str] = set()

        for query in SEARCH_QUERIES:
            try:
                jobs = self._fetch_query(query)
                for job in jobs:
                    job_id = job.url or f"{job.title}|{job.company}"
                    if job_id not in seen_ids:
                        seen_ids.add(job_id)
                        all_jobs.append(job)
                log.info("[JSearch] Query '%s' → %d results", query, len(jobs))
            except Exception as exc:
                log.error("[JSearch] Query '%s' failed: %s", query, exc)
            finally:
                time.sleep(_SLEEP_BETWEEN_QUERIES)

        log.info("[JSearch] Total fetched: %d jobs", len(all_jobs))
        return all_jobs

    # ── Private Methods ───────────────────────────────────────────────────

    @retry_with_backoff(max_attempts=_MAX_RETRIES, exceptions=(requests.RequestException,))
    def _fetch_query(self, query: str) -> List[Job]:
        """Call JSearch /search endpoint for a single query."""
        params = {
            "query": query,
            "page": "1",
            "num_pages": "1",
            "country": "in",        # India
            "date_posted": "today", # Fresh postings only
            "employment_types": "FULLTIME,PARTTIME,CONTRACTOR",
        }
        response = requests.get(
            f"{_API_BASE}/search",
            headers=self._headers,
            params=params,
            timeout=20,
        )
        response.raise_for_status()
        data = response.json()

        if data.get("status") != "OK":
            log.warning("[JSearch] Non-OK status for query '%s': %s", query, data.get("status"))
            return []

        return [self._parse_result(r) for r in data.get("data", []) if r]

    def _parse_result(self, raw: dict) -> Job:
        """Convert a raw JSearch job result into a Job object."""
        # Salary
        min_sal = safe_get(raw, "job_min_salary", default="")
        max_sal = safe_get(raw, "job_max_salary", default="")
        salary_period = safe_get(raw, "job_salary_period", default="")
        if min_sal and max_sal:
            salary = f"₹{min_sal}–₹{max_sal} {salary_period}".strip()
        elif min_sal:
            salary = f"₹{min_sal}+ {salary_period}".strip()
        else:
            salary = ""

        # Employment type
        emp_type = safe_get(raw, "job_employment_type", default="")
        is_remote = raw.get("job_is_remote", False)
        if is_remote:
            job_type = "Remote"
        else:
            job_type = clean_text(str(emp_type).replace("_", " ").title())

        # Location
        city = safe_get(raw, "job_city", default="")
        state = safe_get(raw, "job_state", default="")
        country = safe_get(raw, "job_country", default="India")
        location_parts = [p for p in [city, state, country] if p]
        location = ", ".join(location_parts) or "India"

        return Job(
            title=clean_text(safe_get(raw, "job_title", default="")),
            company=clean_text(safe_get(raw, "employer_name", default="")),
            location=clean_text(location),
            url=safe_get(raw, "job_apply_link", default="") or safe_get(raw, "job_url", default=""),
            description=truncate_text(
                clean_text(safe_get(raw, "job_description", default="")), 800
            ),
            source="jsearch",
            posted_date=safe_get(raw, "job_posted_at_datetime_utc", default=""),
            salary=normalise_salary(salary),
            job_type=job_type,
        )
