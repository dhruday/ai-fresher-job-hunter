"""
services/email_generator.py
────────────────────────────
Renders the Jinja2 HTML email template with job data.

Returns a fully rendered HTML string ready to be sent via mailer.py.
"""

from __future__ import annotations

from collections import Counter
from datetime import date
from typing import List, Tuple

from jinja2 import Environment, FileSystemLoader, select_autoescape

from config.settings import CANDIDATE, TEMPLATES_DIR, get_settings
from services.logger import get_logger
from utils.helpers import Job

log = get_logger(__name__)


class EmailGenerator:
    """Builds the Jinja2 email context and renders the HTML template."""

    def __init__(self) -> None:
        self._settings = get_settings()
        self._env = Environment(
            loader=FileSystemLoader(str(TEMPLATES_DIR)),
            autoescape=select_autoescape(["html", "xml"]),
        )

    # ── Public API ────────────────────────────────────────────────────────

    def generate(self, all_jobs: List[Job]) -> str:
        """
        Generate the full HTML email string.

        Args:
            all_jobs: All AI-scored + filtered jobs (sorted by score desc).

        Returns:
            Rendered HTML string.
        """
        top_jobs = all_jobs[: self._settings.top_jobs_in_email]
        context = self._build_context(all_jobs, top_jobs)
        template = self._env.get_template("email_template.html")
        html = template.render(**context)
        log.info(
            "Email generated: %d top jobs, avg score %s",
            len(top_jobs),
            context["avg_score"],
        )
        return html

    # ── Private Helpers ───────────────────────────────────────────────────

    def _build_context(
        self, all_jobs: List[Job], top_jobs: List[Job]
    ) -> dict:
        """Build the complete template context dictionary."""
        return {
            "candidate_name": CANDIDATE["name"],
            "date": date.today().strftime("%A, %d %B %Y"),
            "total_jobs": len(all_jobs),
            "top_jobs": top_jobs,
            "avg_score": self._avg_score(all_jobs),
            "sources_count": len({j.source for j in all_jobs}),
            "fresher_friendly_count": sum(1 for j in all_jobs if j.fresher_friendly),
            "hiring_trends": self._top_hiring_trends(all_jobs),
            "top_companies": self._top_companies(all_jobs),
            "skill_demand": self._skill_demand(all_jobs),
        }

    @staticmethod
    def _avg_score(jobs: List[Job]) -> int:
        if not jobs:
            return 0
        return round(sum(j.score for j in jobs) / len(jobs))

    @staticmethod
    def _top_hiring_trends(jobs: List[Job]) -> List[Tuple[str, int]]:
        """Return the top 12 most frequent hiring trend tags."""
        counter: Counter = Counter()
        for job in jobs:
            counter.update(job.hiring_trend_tags)
        return counter.most_common(12)

    @staticmethod
    def _top_companies(jobs: List[Job]) -> List[Tuple[str, int]]:
        """Return the top 8 companies with the most listings."""
        counter: Counter = Counter()
        for job in jobs:
            if job.company:
                counter[job.company] += 1
        return [(co, cnt) for co, cnt in counter.most_common(8) if cnt > 1]

    @staticmethod
    def _skill_demand(jobs: List[Job]) -> List[Tuple[str, int]]:
        """
        Estimate demand % for candidate's own skills based on how often
        they appear in hiring_trend_tags across all jobs.
        Returns list of (skill, demand_pct) sorted desc, capped at 100.
        """
        from config.settings import ALL_SKILLS

        total = max(len(jobs), 1)
        skill_hits: Counter = Counter()
        for job in jobs:
            for tag in job.hiring_trend_tags:
                tag_lower = tag.lower()
                for skill in ALL_SKILLS:
                    if skill.lower() in tag_lower or tag_lower in skill.lower():
                        skill_hits[skill] += 1

        result = []
        for skill in ALL_SKILLS:
            count = skill_hits.get(skill, 0)
            pct = min(100, round(count / total * 100))
            result.append((skill, pct))

        result.sort(key=lambda x: x[1], reverse=True)
        return [r for r in result if r[1] > 0][:10]
