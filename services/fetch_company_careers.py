"""
services/fetch_company_careers.py
──────────────────────────────────
Searches GENUINE COMPANY CAREER PORTALS directly via SerpAPI Google search.

WHY THIS IS THE PRIMARY SOURCE:
  - Company career pages have openings *before* they appear on Naukri/LinkedIn
  - Direct application — no middleman, no aggregator delay
  - Targets the exact companies that hire freshers at scale in India
  - No extra API key needed — reuses SERPAPI_KEY

COMPANIES COVERED (50+ top Indian IT + product + startup hirers):
  Tier 1 IT:     TCS, Infosys, Wipro, HCL, Cognizant, Tech Mahindra, Capgemini
  Tier 2 IT:     Mphasis, LTIMindtree, Hexaware, Persistent, Coforge, NIIT Tech
  Product/SaaS:  Zoho, Freshworks, Razorpay, Zepto, Swiggy, Zomato, Paytm
  MNCs:          IBM, Accenture, Deloitte, Goldman Sachs, JP Morgan, SAP
  Startups:      Groww, Cred, PhonePe, Meesho, ShareChat, Ola, Byju's

STRATEGY:
  - 3 targeted query batches: each searches a set of company sites at once
  - Deduplicated by URL before returning
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

# ── Company career portal domains ──────────────────────────────────────────
# Grouped into batches for Google site: OR searches (max ~5 per query)

_COMPANY_GROUPS: list[dict] = [
    {
        "label": "TCS/Infosys/Wipro/HCL/Cognizant",
        "sites": [
            "careers.tcs.com",
            "infosys.com/careers",
            "careers.wipro.com",
            "hcltech.com/careers",
            "careers.cognizant.com",
        ],
    },
    {
        "label": "Capgemini/TechMahindra/Mphasis/LTIMindtree/Persistent",
        "sites": [
            "capgemini.com/in-en/careers",
            "careers.techmahindra.com",
            "careers.mphasis.com",
            "ltimindtree.com/careers",
            "persistent.com/careers",
        ],
    },
    {
        "label": "IBM/Accenture/Deloitte/Oracle/SAP",
        "sites": [
            "ibm.com/in-en/employment",
            "accenture.com/in-en/careers",
            "jobs2.accenture.com",
            "apply.deloitte.com",
            "oracle.com/in/corporate/careers",
        ],
    },
    {
        "label": "Zoho/Freshworks/Razorpay/PhonePe/Groww",
        "sites": [
            "careers.zoho.com",
            "careers.freshworks.com",
            "razorpay.com/careers",
            "phonepe.com/en/careers",
            "groww.in/careers",
        ],
    },
    {
        "label": "Swiggy/Zomato/Meesho/Cred/Paytm",
        "sites": [
            "careers.swiggy.com",
            "zomato.com/careers",
            "meesho.io/careers",
            "careers.cred.club",
            "paytm.com/careers",
        ],
    },
    {
        "label": "Hexaware/Coforge/NIIT/Birlasoft/Zensar",
        "sites": [
            "hexaware.com/careers",
            "coforge.com/careers",
            "niit.com/en/careers",
            "birlasoft.com/careers",
            "zensar.com/careers",
        ],
    },
    {
        "label": "Goldman/JPMorgan/Barclays/Deutsche/Amazon",
        "sites": [
            "goldmansachs.com/careers",
            "jpmorgan.com/global/careers",
            "search.jobs.barclays",
            "db.com/careers",
            "amazon.jobs",
        ],
    },
    {
        "label": "Microsoft/Google/Adobe/Uber/Atlassian",
        "sites": [
            "careers.microsoft.com",
            "careers.google.com",
            "careers.adobe.com",
            "uber.com/in/en/careers",
            "jobs.lever.co/atlassian",
        ],
    },
]

# Role keywords for each search — keeps results relevant to fresher roles
_FRESHER_ROLE_TERMS = (
    '(fresher OR "entry level" OR "0-1 year" OR "0-2 years" OR trainee OR graduate OR associate)'
)
_TECH_TERMS = "(Java OR Python OR React OR \"full stack\" OR \"software engineer\" OR developer)"


class CompanyCareersFetcher:
    """Searches genuine company career portals for fresher openings via SerpAPI."""

    def __init__(self) -> None:
        settings = get_settings()
        self._api_key = settings.serpapi_key
        self._max_results = 10

    # ── Public API ────────────────────────────────────────────────────────

    def fetch_all(self) -> List[Job]:
        all_jobs: List[Job] = []
        seen_urls: set[str] = set()

        for group in _COMPANY_GROUPS:
            try:
                jobs = self._fetch_group(group)
                for job in jobs:
                    if job.url and job.url not in seen_urls:
                        seen_urls.add(job.url)
                        all_jobs.append(job)
                log.info(
                    "[CompanyCareers] Group '%s' → %d results",
                    group["label"], len(jobs),
                )
            except Exception as exc:
                log.error("[CompanyCareers] Group '%s' failed: %s", group["label"], exc)
            finally:
                time.sleep(_SLEEP_BETWEEN_QUERIES)

        log.info("[CompanyCareers] Total fetched: %d jobs", len(all_jobs))
        return all_jobs

    # ── Private Methods ───────────────────────────────────────────────────

    @retry_with_backoff(max_attempts=_MAX_RETRIES, exceptions=(requests.RequestException,))
    def _fetch_group(self, group: dict) -> List[Job]:
        """Build a site: OR query for a company group and fetch results."""
        site_clause = " OR ".join(f"site:{s}" for s in group["sites"])
        query = f"({site_clause}) {_FRESHER_ROLE_TERMS} {_TECH_TERMS} India"

        params = {
            "engine": "google",
            "q": query,
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
            job = self._parse_career_result(r, group["sites"])
            if job:
                jobs.append(job)
        return jobs

    def _parse_career_result(self, raw: dict, company_sites: list[str]) -> Job | None:
        """Convert a Google organic result from a company career page into a Job."""
        url = safe_get(raw, "link", default="")
        if not url:
            return None

        # Verify URL belongs to one of the company sites we searched
        domain_ok = any(domain.split("/")[0] in url for domain in company_sites)
        if not domain_ok:
            return None

        raw_title = clean_text(safe_get(raw, "title", default=""))
        snippet = clean_text(safe_get(raw, "snippet", default=""))

        # Clean trailing " | Careers" / " - Careers" suffixes
        title = re.sub(
            r"\s*[\|\-–]\s*(career[s]?|jobs?|apply|opportunities).*$",
            "",
            raw_title,
            flags=re.IGNORECASE,
        ).strip()
        if not title:
            title = raw_title[:80] or "Job Opening"

        # Detect company name from URL domain
        company = _company_from_url(url, company_sites)

        # Extract location from snippet
        location = _extract_location(snippet + " " + raw_title)

        # Salary / job type from snippet
        salary = normalise_salary(_extract_salary(snippet))

        return Job(
            title=title,
            company=company,
            location=location,
            url=url,
            description=truncate_text(snippet, 800),
            source="company_careers",
            posted_date="",
            salary=salary,
            job_type="",
        )


# ── Helpers ───────────────────────────────────────────────────────────────

_INDIA_CITIES = re.compile(
    r"\b(Bangalore|Bengaluru|Hyderabad|Chennai|Mumbai|Pune|Delhi|Noida|"
    r"Gurgaon|Gurugram|Kolkata|Ahmedabad|India|Remote|Hybrid|Kochi|"
    r"Trivandrum|Thiruvananthapuram|Mysore|Coimbatore|Jaipur|Chandigarh)\b",
    re.IGNORECASE,
)

_SALARY_RE = re.compile(
    r"(?:₹|INR|Rs\.?)\s*[\d,.]+\s*(?:L|lakh|LPA|per annum|/yr)?",
    re.IGNORECASE,
)

# Map domain keywords to friendly company names
_DOMAIN_TO_COMPANY = {
    "tcs": "TCS", "infosys": "Infosys", "wipro": "Wipro",
    "hcltech": "HCL Technologies", "cognizant": "Cognizant",
    "capgemini": "Capgemini", "techmahindra": "Tech Mahindra",
    "mphasis": "Mphasis", "ltimindtree": "LTIMindtree",
    "persistent": "Persistent Systems", "ibm": "IBM",
    "accenture": "Accenture", "deloitte": "Deloitte",
    "oracle": "Oracle", "zoho": "Zoho", "freshworks": "Freshworks",
    "razorpay": "Razorpay", "phonepe": "PhonePe", "groww": "Groww",
    "swiggy": "Swiggy", "zomato": "Zomato", "meesho": "Meesho",
    "cred": "CRED", "paytm": "Paytm", "hexaware": "Hexaware",
    "coforge": "Coforge", "niit": "NIIT", "birlasoft": "Birlasoft",
    "zensar": "Zensar", "goldmansachs": "Goldman Sachs",
    "jpmorgan": "JP Morgan", "barclays": "Barclays",
    "microsoft": "Microsoft", "google": "Google", "adobe": "Adobe",
    "uber": "Uber", "atlassian": "Atlassian", "amazon": "Amazon",
    "sap": "SAP", "db.com": "Deutsche Bank",
}


def _company_from_url(url: str, sites: list[str]) -> str:
    for key, name in _DOMAIN_TO_COMPANY.items():
        if key in url.lower():
            return name
    # Fallback: extract second-level domain
    m = re.search(r"https?://(?:careers?\.)?([a-z0-9\-]+)\.", url, re.IGNORECASE)
    return m.group(1).title() if m else "Company"


def _extract_location(text: str) -> str:
    m = _INDIA_CITIES.search(text)
    return m.group(0).strip() if m else "India"


def _extract_salary(text: str) -> str:
    m = _SALARY_RE.search(text)
    return m.group(0).strip() if m else ""
