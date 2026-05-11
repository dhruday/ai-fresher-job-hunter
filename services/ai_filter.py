"""
services/ai_filter.py
──────────────────────
Uses Groq (free) or OpenAI GPT-4o-mini to score, rank, and enrich job listings.

PROVIDER PRIORITY:
  1. Groq  — free tier, 14,400 req/day, llama-3.1-8b-instant, OpenAI-compatible
  2. OpenAI — fallback if GROQ_API_KEY is not set (requires billing)

STRATEGY:
  1. Pre-filter with regex  → rejects obvious mismatches cheaply
  2. Batch scoring          → 20 jobs per call (reduces token overhead)
  3. Structured JSON output → parse reliably
  4. Reject score < MIN_AI_SCORE after scoring

COST: FREE on Groq (14,400 req/day free tier — ~8 calls/day used)
"""

from __future__ import annotations

import json
import time
from typing import Any, List

from openai import OpenAI, RateLimitError

from config.settings import ALL_SKILLS, CANDIDATE, get_settings
from services.logger import get_logger
from utils.helpers import Job, quick_reject, truncate_text

log = get_logger(__name__)

# ── Prompt Templates ───────────────────────────────────────────────────────

_SYSTEM_PROMPT = """\
You are an expert technical recruiter specializing in entry-level software jobs in India.
Evaluate each job for a fresher candidate with the profile below and return a structured JSON array.

CANDIDATE PROFILE:
Name: {name}
Experience: Fresher (0 years)
Target roles: {target_roles}
Skills: {skills}
Preferred locations: Bangalore/Bengaluru (FIRST PREFERENCE), Remote, Hybrid, India

SCORING RUBRIC (score 1–100):
90–100: Perfect match — exact title, all core skills present, fresher/entry-level, Bangalore/Bengaluru
75–89:  Great match — Bangalore/Bengaluru or Remote, close title, most skills present, fresher-friendly
60–74:  Good match — other India cities, close title, most skills present, fresher-friendly
50–59:  Partial match — related title, some skills overlap, may need learning
40–49:  Weak match — somewhat related, few skills match, but worth considering
1–39:   Poor match — reject (irrelevant stack, senior role, wrong location)

LOCATION BONUS: Add +8 to the score if the job is in Bangalore or Bengaluru. Add +4 for Remote/Hybrid.
LOCATION PENALTY: Subtract -5 if the job is strictly outside India.

RETURN FORMAT (strict JSON object with key "jobs", no markdown):
{{"jobs": [
  {{
    "index": 0,
    "score": 85,
    "skill_match_pct": 75,
    "fresher_friendly": true,
    "missing_skills": ["Docker", "Kubernetes"],
    "ai_summary": "Great React.js role at a startup, 0-1 yrs experience, Bangalore",
    "hiring_trend_tags": ["React", "Full Stack", "Startup"]
  }}
]}}

Rules:
- Return EXACTLY one JSON object per job in the input, indexed from 0.
- ai_summary must be <= 120 characters.
- hiring_trend_tags: max 4 short tags.
- missing_skills: only from candidate's known tech ecosystem (don't invent tools).
- If a job is clearly for seniors/leads, give score <= 20.
"""

_USER_PROMPT_TEMPLATE = """\
Evaluate these {count} jobs and return the JSON object:

{jobs_json}
"""


def _build_client(settings) -> tuple[OpenAI, str]:
    """Prefer Groq (free); fall back to OpenAI. Returns (client, model_name)."""
    if settings.groq_api_key:
        log.info("LLM provider: Groq (free) — model: %s", settings.groq_model)
        return OpenAI(api_key=settings.groq_api_key, base_url=settings.groq_base_url), settings.groq_model
    if settings.openai_api_key:
        log.info("LLM provider: OpenAI — model: %s", settings.openai_model)
        return OpenAI(api_key=settings.openai_api_key), settings.openai_model
    raise RuntimeError(
        "No LLM API key found. Set GROQ_API_KEY (free) or OPENAI_API_KEY in .env"
    )


class AIFilter:
    """Scores and enriches jobs using Groq (free) or OpenAI in efficient batches."""

    def __init__(self) -> None:
        settings = get_settings()
        self._client, self._model = _build_client(settings)
        self._batch_size = settings.openai_batch_size
        self._max_tokens = settings.openai_max_tokens_per_batch
        self._temperature = settings.openai_temperature
        self._min_score = settings.min_ai_score
        self._is_groq = bool(settings.groq_api_key)

    # ── Public API ────────────────────────────────────────────────────────

    def filter_and_score(self, jobs: List[Job]) -> List[Job]:
        """
        1. Pre-filter with regex (fast, free)
        2. Batch-score remaining jobs with GPT-4o-mini
        3. Reject jobs below MIN_AI_SCORE
        4. Return sorted list (highest score first)
        """
        # Step 1: Cheap pre-filter
        pre_filtered = [j for j in jobs if not quick_reject(j)]
        rejected_count = len(jobs) - len(pre_filtered)
        log.info(
            "Pre-filter: %d jobs in → %d passed → %d rejected by regex",
            len(jobs), len(pre_filtered), rejected_count,
        )

        if not pre_filtered:
            log.warning("No jobs survived pre-filter — returning empty list")
            return []

        # Step 2: AI scoring in batches
        scored_jobs = self._score_in_batches(pre_filtered)

        # Step 3: Apply score threshold
        passing = [j for j in scored_jobs if j.score >= self._min_score]
        log.info(
            "AI scoring: %d scored → %d passed threshold (score >= %d)",
            len(scored_jobs), len(passing), self._min_score,
        )

        # Step 4: Sort by score descending
        passing.sort(key=lambda j: j.score, reverse=True)
        return passing

    # ── Private Methods ───────────────────────────────────────────────────

    def _score_in_batches(self, jobs: List[Job]) -> List[Job]:
        """Split jobs into batches, call GPT for each, merge results."""
        result: List[Job] = []

        for batch_start in range(0, len(jobs), self._batch_size):
            batch = jobs[batch_start : batch_start + self._batch_size]
            try:
                scored_batch = self._score_batch(batch)
                result.extend(scored_batch)
                log.debug(
                    "Batch %d-%d scored successfully",
                    batch_start, batch_start + len(batch) - 1,
                )
            except Exception as exc:
                log.error(
                    "AI scoring batch %d-%d failed: %s — using default scores",
                    batch_start, batch_start + len(batch) - 1, exc,
                )
                # Don't drop jobs — give them a default score so they appear
                for job in batch:
                    job.score = 50
                    job.ai_summary = "AI scoring unavailable for this job."
                result.extend(batch)

            # Rate-limit guard — Groq: 6000 tokens/min; 1.5s gap keeps us safe
            if batch_start + self._batch_size < len(jobs):
                time.sleep(1.5 if self._is_groq else 2)

        return result

    def _score_batch(self, jobs: List[Job]) -> List[Job]:
        """Call GPT-4o-mini once for a batch of jobs; parse and apply results."""
        jobs_payload = [
            {
                "index": i,
                "title": job.title,
                "company": job.company,
                "location": job.location,
                "description": truncate_text(job.description, 400),
                "salary": job.salary,
                "job_type": job.job_type,
                "source": job.source,
            }
            for i, job in enumerate(jobs)
        ]

        system_prompt = _SYSTEM_PROMPT.format(
            name=CANDIDATE["name"],
            target_roles=", ".join(CANDIDATE["target_roles"]),
            skills=", ".join(ALL_SKILLS),
        )
        user_prompt = _USER_PROMPT_TEMPLATE.format(
            count=len(jobs),
            jobs_json=json.dumps(jobs_payload, ensure_ascii=False, indent=2),
        )

        try:
            response = self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=self._temperature,
                max_tokens=self._max_tokens,
                response_format={"type": "json_object"},
            )
        except RateLimitError:
            wait = 15 if self._is_groq else 60
            log.warning("Rate limit hit — waiting %d seconds", wait)
            time.sleep(wait)
            raise  # tenacity will retry

        raw_content = response.choices[0].message.content or "{}"
        return self._parse_ai_response(raw_content, jobs)

    def _parse_ai_response(self, raw: str, jobs: List[Job]) -> List[Job]:
        """
        Parse the JSON response from GPT and apply scores/metadata to jobs.
        Handles both {"jobs": [...]} and [...] formats defensively.
        """
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as e:
            log.error("Failed to parse AI response JSON: %s\nRaw: %s", e, raw[:500])
            # Fall back: give all jobs a neutral score
            for job in jobs:
                job.score = 50
                job.ai_summary = "AI scoring parse error."
            return jobs

        # GPT sometimes wraps the array in an object key
        if isinstance(parsed, dict):
            # Common keys: "jobs", "results", "data", "evaluations"
            for key in ("jobs", "results", "data", "evaluations", "items"):
                if isinstance(parsed.get(key), list):
                    parsed = parsed[key]
                    break
            else:
                # Last resort: take the first list value
                for v in parsed.values():
                    if isinstance(v, list):
                        parsed = v
                        break
                else:
                    parsed = []

        if not isinstance(parsed, list):
            log.error("AI response is not a list: %s", type(parsed))
            for job in jobs:
                job.score = 50
            return jobs

        # Apply scores
        index_map: dict[int, Any] = {item.get("index", i): item for i, item in enumerate(parsed)}
        for i, job in enumerate(jobs):
            item = index_map.get(i, {})
            job.score = max(0, min(100, int(item.get("score", 50))))
            job.skill_match_pct = max(0, min(100, int(item.get("skill_match_pct", 0))))
            job.fresher_friendly = bool(item.get("fresher_friendly", False))
            job.missing_skills = list(item.get("missing_skills", []))[:6]
            job.ai_summary = str(item.get("ai_summary", ""))[:150]
            job.hiring_trend_tags = list(item.get("hiring_trend_tags", []))[:4]

        return jobs
