"""
services/storage.py
───────────────────
Handles persisting filtered & AI-scored jobs to:

  1. Daily CSV   → data/jobs_YYYY-MM-DD.csv
  2. Master CSV  → data/master_jobs.csv  (append-only, all-time)
  3. Google Sheets (optional) — activated by GOOGLE_SHEETS_ID env var

Usage:
    storage = Storage()
    storage.save(jobs)
"""

from __future__ import annotations

import base64
import csv
import json
import tempfile
from datetime import date
from pathlib import Path
from typing import List, Optional

from config.settings import DATA_DIR, get_settings
from services.logger import get_logger
from utils.helpers import Job

log = get_logger(__name__)

_CSV_FIELDNAMES = [
    "id", "title", "company", "location", "url", "source",
    "posted_date", "salary", "job_type",
    "score", "skill_match_pct", "fresher_friendly",
    "missing_skills", "ai_summary", "hiring_trend_tags",
    "description",
]


class Storage:
    """Saves jobs to local CSV and optionally Google Sheets."""

    def __init__(self) -> None:
        self._settings = get_settings()
        DATA_DIR.mkdir(parents=True, exist_ok=True)

    # ── Public API ────────────────────────────────────────────────────────

    def save(self, jobs: List[Job]) -> Path:
        """
        Save *jobs* to daily + master CSV.
        Optionally append to Google Sheets if configured.
        Returns the path to the daily CSV file.
        """
        if not jobs:
            log.info("No jobs to save — skipping storage")
            return DATA_DIR / f"jobs_{date.today().isoformat()}.csv"

        daily_path = self._write_daily_csv(jobs)
        self._append_master_csv(jobs)

        if self._settings.google_sheets_id:
            try:
                self._append_google_sheets(jobs)
            except Exception as exc:
                log.warning("Google Sheets upload failed (non-fatal): %s", exc)

        return daily_path

    # ── CSV Writers ───────────────────────────────────────────────────────

    def _write_daily_csv(self, jobs: List[Job]) -> Path:
        """Write all jobs to a dated CSV, overwriting if it exists."""
        filename = DATA_DIR / f"jobs_{date.today().isoformat()}.csv"
        self._write_csv(filename, jobs, mode="w")
        log.info("Daily CSV written: %s (%d jobs)", filename.name, len(jobs))
        return filename

    def _append_master_csv(self, jobs: List[Job]) -> None:
        """Append jobs to the all-time master CSV."""
        master_path = DATA_DIR / "master_jobs.csv"
        write_header = not master_path.exists()
        self._write_csv(master_path, jobs, mode="a", write_header=write_header)
        log.info("Master CSV updated: %s", master_path.name)

    def _write_csv(
        self,
        path: Path,
        jobs: List[Job],
        mode: str = "w",
        write_header: bool = True,
    ) -> None:
        with path.open(mode, newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(
                f, fieldnames=_CSV_FIELDNAMES, extrasaction="ignore"
            )
            if write_header:
                writer.writeheader()
            for job in jobs:
                writer.writerow(job.to_dict())

    # ── Google Sheets ─────────────────────────────────────────────────────

    def _append_google_sheets(self, jobs: List[Job]) -> None:
        """
        Append rows to Google Sheets using a service-account credential.
        Requires:
          - GOOGLE_SHEETS_ID
          - GOOGLE_SERVICE_ACCOUNT_JSON (base64-encoded service account JSON)
        """
        try:
            import gspread
            from google.oauth2.service_account import Credentials
        except ImportError:
            log.warning("gspread / google-auth not installed — skipping Sheets upload")
            return

        raw_b64 = self._settings.google_service_account_json
        if not raw_b64:
            log.warning("GOOGLE_SERVICE_ACCOUNT_JSON is empty — skipping Sheets")
            return

        # Decode base64 service account JSON (safe — no eval, no exec)
        try:
            sa_json = json.loads(base64.b64decode(raw_b64).decode("utf-8"))
        except Exception as exc:
            log.error("Failed to decode GOOGLE_SERVICE_ACCOUNT_JSON: %s", exc)
            return

        scopes = [
            "https://spreadsheets.google.com/feeds",
            "https://www.googleapis.com/auth/drive",
        ]
        creds = Credentials.from_service_account_info(sa_json, scopes=scopes)
        gc = gspread.authorize(creds)

        sh = gc.open_by_key(self._settings.google_sheets_id)
        worksheet = sh.sheet1  # first sheet

        # Ensure header row exists
        existing = worksheet.get_all_values()
        if not existing:
            worksheet.append_row(_CSV_FIELDNAMES)

        rows = [[str(job.to_dict().get(f, "")) for f in _CSV_FIELDNAMES] for job in jobs]
        worksheet.append_rows(rows, value_input_option="RAW")
        log.info("Appended %d rows to Google Sheets", len(rows))
