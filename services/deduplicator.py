"""
services/deduplicator.py
────────────────────────
Two-level deduplication:

  1. Within-run dedup  — in-memory set; eliminates duplicates that appear
                         across multiple fetchers in the same run.

  2. Cross-day dedup   — SQLite database at data/seen_jobs.db; keeps a
                         rolling 30-day window of seen job hashes so that
                         the same job is not emailed on consecutive days.

Usage:
    dedup = Deduplicator()
    new_jobs = dedup.filter_new(all_jobs)
    dedup.mark_seen(new_jobs)
    dedup.close()
"""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from typing import List

from config.settings import DB_PATH
from services.logger import get_logger
from utils.helpers import Job, generate_job_hash

log = get_logger(__name__)

_RETENTION_DAYS = 30       # purge records older than this
_SCHEMA = """
CREATE TABLE IF NOT EXISTS seen_jobs (
    hash        TEXT PRIMARY KEY,
    title       TEXT,
    company     TEXT,
    first_seen  TEXT,
    last_seen   TEXT
);
"""


class Deduplicator:
    """Thread-safe (single-connection) deduplication store."""

    def __init__(self) -> None:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.execute(_SCHEMA)
        self._conn.commit()
        self._purge_old_records()
        log.debug("Deduplicator initialised — DB: %s", DB_PATH)

    # ── Public API ────────────────────────────────────────────────────────

    def filter_new(self, jobs: List[Job]) -> List[Job]:
        """
        Return only jobs not already seen (both within-run and cross-day).
        Assign each job's `id` field (hash) as a side-effect.
        """
        seen_hashes: set[str] = set()
        new_jobs: List[Job] = []

        # Load today's DB hashes once
        db_hashes = self._load_db_hashes()

        for job in jobs:
            h = generate_job_hash(job.title, job.company, job.location)
            job.id = h  # always stamp the id

            if h in seen_hashes:
                log.debug("Within-run duplicate skipped: %s @ %s", job.title, job.company)
                continue
            if h in db_hashes:
                log.debug("Cross-day duplicate skipped: %s @ %s", job.title, job.company)
                continue

            seen_hashes.add(h)
            new_jobs.append(job)

        log.info(
            "Deduplication: %d total → %d new (skipped %d duplicates)",
            len(jobs),
            len(new_jobs),
            len(jobs) - len(new_jobs),
        )
        return new_jobs

    def mark_seen(self, jobs: List[Job]) -> None:
        """Persist job hashes to SQLite so future runs skip them."""
        today = date.today().isoformat()
        rows = [
            (job.id, job.title[:200], job.company[:200], today, today)
            for job in jobs
        ]
        self._conn.executemany(
            """
            INSERT INTO seen_jobs (hash, title, company, first_seen, last_seen)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(hash) DO UPDATE SET last_seen = excluded.last_seen
            """,
            rows,
        )
        self._conn.commit()
        log.info("Marked %d jobs as seen in DB", len(rows))

    def close(self) -> None:
        """Cleanly close the SQLite connection."""
        self._conn.close()

    # ── Private Helpers ───────────────────────────────────────────────────

    def _load_db_hashes(self) -> set[str]:
        cursor = self._conn.execute("SELECT hash FROM seen_jobs")
        return {row[0] for row in cursor.fetchall()}

    def _purge_old_records(self) -> None:
        """Remove records older than *_RETENTION_DAYS* to keep DB lean."""
        cutoff = (date.today() - timedelta(days=_RETENTION_DAYS)).isoformat()
        cursor = self._conn.execute(
            "DELETE FROM seen_jobs WHERE last_seen < ?", (cutoff,)
        )
        self._conn.commit()
        if cursor.rowcount:
            log.debug("Purged %d old job records from DB", cursor.rowcount)
