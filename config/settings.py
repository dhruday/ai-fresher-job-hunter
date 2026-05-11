"""
config/settings.py
──────────────────
Single source of truth for all configuration, constants, and
candidate profile data.  Loaded once at import time via pydantic-settings.
"""

from __future__ import annotations

from functools import lru_cache
from typing import List

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings — values come from environment / .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── API Keys ──────────────────────────────────────────────────────────
    # Groq is the primary LLM provider (free tier, OpenAI-compatible API)
    groq_api_key: str = Field(default="", alias="GROQ_API_KEY")
    # OpenAI kept as optional fallback
    openai_api_key: str = Field(default="", alias="OPENAI_API_KEY")
    serpapi_key: str = Field(..., alias="SERPAPI_KEY")
    rapidapi_key: str = Field(..., alias="RAPIDAPI_KEY")
    apify_token: str = Field(default="", alias="APIFY_TOKEN")

    # ── Email ─────────────────────────────────────────────────────────────
    email_user: str = Field(..., alias="EMAIL_USER")
    email_pass: str = Field(..., alias="EMAIL_PASS")
    my_email: str = Field(..., alias="MY_EMAIL")

    # ── Telegram (optional) ───────────────────────────────────────────────
    telegram_bot_token: str = Field(default="", alias="TELEGRAM_BOT_TOKEN")
    telegram_chat_id: str = Field(default="", alias="TELEGRAM_CHAT_ID")

    # ── Google Sheets (optional) ──────────────────────────────────────────
    google_sheets_id: str = Field(default="", alias="GOOGLE_SHEETS_ID")
    google_service_account_json: str = Field(
        default="", alias="GOOGLE_SERVICE_ACCOUNT_JSON"
    )

    # ── Tuning ────────────────────────────────────────────────────────────
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    max_jobs_per_source: int = Field(default=25, alias="MAX_JOBS_PER_SOURCE")
    min_ai_score: int = Field(default=40, alias="MIN_AI_SCORE")
    top_jobs_in_email: int = Field(default=20, alias="TOP_JOBS_IN_EMAIL")

    # ── LLM Settings (Groq by default, OpenAI as fallback) ──────────────
    # Groq free models: https://console.groq.com/docs/models
    groq_model: str = "llama-3.1-8b-instant"   # fast, free, great at JSON
    groq_base_url: str = "https://api.groq.com/openai/v1"
    # OpenAI fallback model
    openai_model: str = "gpt-4o-mini"
    openai_max_tokens_per_batch: int = 2000
    openai_batch_size: int = 20          # jobs per GPT/Groq call
    openai_temperature: float = 0.1      # low variance for scoring tasks


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a cached Settings instance (reads .env once)."""
    return Settings()  # type: ignore[call-arg]


# ── Candidate Profile ──────────────────────────────────────────────────────

CANDIDATE = {
    "name": "Kasamsetty Raja Rajeswari",
    "target_roles": [
        "Java Full Stack Developer",
        "Associate Software Engineer",
        "Software Engineer Fresher",
        "Frontend Developer",
        "Backend Developer",
    ],
    "skills": {
        "frontend": ["HTML", "CSS", "JavaScript", "React.js"],
        "backend": ["Java", "Spring Boot", "REST APIs"],
        "database": ["MySQL", "PostgreSQL"],
        "ai_ml": ["Python", "Machine Learning"],
        "tools": ["Git", "GitHub", "Docker", "Postman"],
        "cloud": ["AWS"],
    },
    "experience": "Fresher / 0 years",
    "preferred_locations": ["Bangalore", "Bengaluru", "Remote", "Hybrid", "India"],
}

# Flattened skill list for AI prompts
ALL_SKILLS: List[str] = [
    skill for group in CANDIDATE["skills"].values() for skill in group
]


# ── Search Queries ─────────────────────────────────────────────────────────

SEARCH_QUERIES: List[str] = [
    "Java Full Stack Developer Fresher India",
    "React Developer Fresher India",
    "Spring Boot Fresher India",
    "Associate Software Engineer India",
    "Software Engineer Fresher India",
    "Frontend Developer Fresher India",
    "Backend Developer Fresher India",
    "Python Developer Fresher India",
]


# ── Job Filter Keywords ────────────────────────────────────────────────────

# Reject jobs whose title contains any of these (case-insensitive)
REJECT_TITLE_KEYWORDS: List[str] = [
    "senior",
    "lead",
    "principal",
    "staff",
    "manager",
    "director",
    "head of",
    "vp ",
    "vice president",
    "architect",   # typically 5+ yrs
    "consultant",
    "specialist",  # often mid-level
    "expert",
]

# Reject jobs whose description matches these experience patterns
REJECT_EXPERIENCE_PATTERNS: List[str] = [
    r"\b[3-9]\+?\s*years?\b",
    r"\b1[0-9]\+?\s*years?\b",
    r"\bminimum\s+[3-9]\s+years?\b",
    r"\bat\s+least\s+[3-9]\s+years?\b",
    r"\b[3-9]\s*-\s*\d+\s*years?\s*(of\s+)?experience\b",
]

# Accept these — used in AI prompt to bias towards fresher roles
ACCEPT_KEYWORDS: List[str] = [
    "fresher",
    "entry level",
    "entry-level",
    "0-2 years",
    "0 to 2 years",
    "graduate",
    "trainee",
    "junior",
    "associate",
    "campus",
    "recent graduate",
    "no experience required",
]


# ── Paths ──────────────────────────────────────────────────────────────────

from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
LOGS_DIR = BASE_DIR / "logs"
TEMPLATES_DIR = BASE_DIR / "templates"
DB_PATH = DATA_DIR / "seen_jobs.db"

# Ensure runtime directories exist
DATA_DIR.mkdir(exist_ok=True)
LOGS_DIR.mkdir(exist_ok=True)
