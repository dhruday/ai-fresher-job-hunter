"""
utils/helpers.py
────────────────
Shared utility functions used across the entire project:
  - Shared Job dataclass (single source of truth)
  - retry_with_backoff decorator via tenacity
  - Text cleaning / normalisation helpers
  - Job hash generation (for deduplication)
  - Salary normalisation
  - HTML tag stripping
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any, Callable, List, Optional, TypeVar

from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
    before_sleep_log,
)
import logging

logger = logging.getLogger(__name__)

F = TypeVar("F", bound=Callable[..., Any])


# ── Shared Job Data Model ──────────────────────────────────────────────────

@dataclass
class Job:
    """
    Canonical job representation shared by all fetchers and services.
    AI-scored fields default to sentinel values and are populated by ai_filter.
    """

    # Core fields (populated by fetchers)
    id: str = ""
    title: str = ""
    company: str = ""
    location: str = ""
    url: str = ""
    description: str = ""
    source: str = ""            # "google_jobs" | "jsearch" | "linkedin"
    posted_date: str = ""
    salary: str = ""
    job_type: str = ""          # "Full-time", "Part-time", "Remote", etc.

    # AI-enriched fields (populated by ai_filter)
    score: int = 0
    skill_match_pct: int = 0
    fresher_friendly: bool = False
    missing_skills: List[str] = field(default_factory=list)
    ai_summary: str = ""
    hiring_trend_tags: List[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Convert to a plain dict for CSV / Sheets serialisation."""
        return {
            "id": self.id,
            "title": self.title,
            "company": self.company,
            "location": self.location,
            "url": self.url,
            "description": self.description[:500],  # truncate for storage
            "source": self.source,
            "posted_date": self.posted_date,
            "salary": self.salary,
            "job_type": self.job_type,
            "score": self.score,
            "skill_match_pct": self.skill_match_pct,
            "fresher_friendly": self.fresher_friendly,
            "missing_skills": ", ".join(self.missing_skills),
            "ai_summary": self.ai_summary,
            "hiring_trend_tags": ", ".join(self.hiring_trend_tags),
        }


# ── Retry Decorator ────────────────────────────────────────────────────────

def retry_with_backoff(
    max_attempts: int = 3,
    min_wait: float = 1.0,
    max_wait: float = 10.0,
    exceptions: tuple[type[Exception], ...] = (Exception,),
) -> Callable[[F], F]:
    """
    Decorator: retries the function up to *max_attempts* times with
    exponential back-off.  Logs each retry attempt at WARNING level.

    Usage:
        @retry_with_backoff(max_attempts=3, exceptions=(requests.RequestException,))
        def call_api():
            ...
    """
    return retry(
        stop=stop_after_attempt(max_attempts),
        wait=wait_exponential(multiplier=1, min=min_wait, max=max_wait),
        retry=retry_if_exception_type(exceptions),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True,
    )


# ── Text Cleaning ──────────────────────────────────────────────────────────

_HTML_TAG_RE = re.compile(r"<[^>]+>")
_WHITESPACE_RE = re.compile(r"\s+")


def strip_html_tags(text: str) -> str:
    """Remove HTML tags from a string."""
    return _HTML_TAG_RE.sub(" ", text or "").strip()


def clean_text(text: str) -> str:
    """Strip HTML, collapse whitespace, and strip leading/trailing space."""
    text = strip_html_tags(text)
    return _WHITESPACE_RE.sub(" ", text).strip()


def truncate_text(text: str, max_chars: int = 800) -> str:
    """Truncate text to *max_chars* characters, appending '…' if cut."""
    text = clean_text(text)
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rsplit(" ", 1)[0] + "…"


# ── Job Hash ───────────────────────────────────────────────────────────────

def generate_job_hash(title: str, company: str, location: str) -> str:
    """
    Generate a stable 12-character hex hash from job identity fields.
    Used for deduplication across fetchers and across days.
    """
    normalised = f"{title.lower().strip()}|{company.lower().strip()}|{location.lower().strip()}"
    return hashlib.sha256(normalised.encode()).hexdigest()[:12]


# ── Salary Normalisation ───────────────────────────────────────────────────

_SALARY_RANGE_RE = re.compile(
    r"(?:₹|Rs\.?|INR|USD|\$)?\s*(\d[\d,\.]*)\s*(?:–|-|to)\s*(?:₹|Rs\.?|INR|USD|\$)?\s*(\d[\d,\.]*)",
    re.IGNORECASE,
)


def normalise_salary(raw: str) -> str:
    """
    Return a cleaned salary string.  Returns 'Not specified' if empty.
    """
    if not raw or raw.strip() in ("", "N/A", "null", "None"):
        return "Not specified"
    cleaned = clean_text(raw)
    # Cap to reasonable length
    return cleaned[:120] if len(cleaned) > 120 else cleaned


# ── Experience Extractor ───────────────────────────────────────────────────

_EXP_PATTERN = re.compile(
    r"(\d+)\s*(?:–|-|to)?\s*(\d+)?\s*(?:\+)?\s*(?:years?|yrs?)\s*(?:of\s+)?(?:experience)?",
    re.IGNORECASE,
)


def extract_max_experience(text: str) -> Optional[int]:
    """
    Extract the maximum years of experience required from free text.
    Returns None if not found.
    """
    matches = _EXP_PATTERN.findall(text)
    if not matches:
        return None
    # Take the highest number found
    max_exp = 0
    for match in matches:
        for val in match:
            try:
                max_exp = max(max_exp, int(val))
            except ValueError:
                pass
    return max_exp if max_exp > 0 else None


# ── Pre-filter (fast, before AI) ──────────────────────────────────────────

from config.settings import REJECT_TITLE_KEYWORDS, REJECT_EXPERIENCE_PATTERNS


def quick_reject(job: Job) -> bool:
    """
    Return True if the job should be *rejected* without sending to AI.
    This is a cheap regex-based pre-filter to reduce token cost.
    """
    title_lower = job.title.lower()
    for kw in REJECT_TITLE_KEYWORDS:
        if kw in title_lower:
            return True

    combined_text = f"{job.title} {job.description}".lower()
    for pattern in REJECT_EXPERIENCE_PATTERNS:
        if re.search(pattern, combined_text, re.IGNORECASE):
            return True

    return False


def safe_get(d: dict, *keys: str, default: Any = "") -> Any:
    """Safely retrieve a nested dictionary value."""
    current = d
    for key in keys:
        if not isinstance(current, dict):
            return default
        current = current.get(key, default)
    return current if current is not None else default
