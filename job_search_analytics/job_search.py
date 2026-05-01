import os
import re
import time
import hashlib
import requests
import feedparser
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timezone

# ── Credentials ────────────────────────────────────────────────────────────────
ADZUNA_APP_ID      = os.environ["ADZUNA_APP_ID"]
ADZUNA_API_KEY     = os.environ["ADZUNA_API_KEY"]
GMAIL_USER         = os.environ["GMAIL_USER"]
GMAIL_APP_PASSWORD = os.environ["GMAIL_APP_PASSWORD"]
RECIPIENT_EMAILS   = [
    e.strip() for e in os.environ.get("RECIPIENT_EMAIL", GMAIL_USER).split(",")
]
SERPAPI_KEY = os.environ.get("SERPAPI_KEY", "")

ADZUNA_BASE = "https://api.adzuna.com/v1/api/jobs/us/search"

# ── Search Queries ─────────────────────────────────────────────────────────────
ADZUNA_QUERIES = [
    "Director of Analytics",
    "Director Business Intelligence",
    "Associate Director Analytics",
    "Senior Manager Analytics",
    "Senior Manager Business Intelligence",
    "Principal Data Analyst",
    "Principal Analytics Engineer",
    "Head of Analytics",
    "Lead Business Analyst",
    "Principal Business Analyst",
    "Analytics Business Partner",
    "Senior Lead BI Analyst",
]

INDEED_QUERIES = [
    "Director of Analytics",
    "Director Business Intelligence",
    "Principal Data Analyst",
    "Head of Analytics",
    "Senior Manager Analytics",
    "Principal Analytics Engineer",
    "Analytics Business Partner",
    "Lead Business Analyst",
    "Associate Director Analytics",
]

# (search_term, company_name_hint) — searched on Indeed RSS by company
COMPANY_QUERIES = [
    ("analytics director", "Wellington Management"),
    ("analytics director", "Fidelity"),
    ("analytics director", "State Street"),
    ("analytics", "MFS Investment"),
    ("analytics director", "Clean Harbors"),
    ("analytics director", "Planet Fitness"),
    ("analytics director", "Staples"),
    ("analytics", "Waters Corporation"),
    ("analytics director", "TJX"),
    ("analytics director", "Hologic"),
]

ADZUNA_LOCATIONS = ["Boston", "Massachusetts"]

# ── Title Match Patterns ───────────────────────────────────────────────────────
# A job must match ≥1 of these (evaluated on the job title, case-insensitive)
TITLE_PATTERNS = [
    r"\b(associate\s+)?director\b.{0,40}\b(analytics|business intelligence|bi|data)\b",
    r"\b(analytics|business intelligence|bi|data)\b.{0,40}\b(associate\s+)?director\b",
    r"\b(sr\.?\s*|senior\s+)(manager|mgr)\b.{0,30}\b(analytics|business intelligence|bi|data)\b",
    r"\b(analytics|business intelligence|bi|data)\b.{0,30}\b(sr\.?\s*|senior\s+)(manager|mgr)\b",
    r"\bprincipal\s+(data analyst|analytics engineer|business analyst|bi analyst)\b",
    r"\bhead\s+of\s+(analytics|bi|business intelligence|data)\b",
    r"\b(lead|principal)\s+business\s+analyst\b",
    r"\bsenior\s+lead\s+(bi|business intelligence)\s+analyst\b",
    r"\banalytics\s+business\s+partner\b",
    r"\b(vp|vice\s*president)\b.{0,30}\b(analytics|data|bi|business intelligence)\b",
]

# ── Must-Have Keywords (≥2 groups must appear in title + description) ──────────
MUST_HAVE_GROUPS = [
    ["snowflake"],
    ["power bi", "powerbi", "tableau"],
    ["sql"],
    ["analytics strategy", "bi strategy", "data strategy", "analytics roadmap", "data roadmap"],
    [
        "team leadership", "people management", "lead a team", "manage a team",
        "managing a team", "direct reports", "people leader", "manage analysts",
        "lead analysts", "leadership experience", "manage team of", "lead team of",
    ],
]
MUST_HAVE_MIN = 2          # minimum groups that must match
SHORT_DESC_THRESHOLD = 200  # skip keyword check if description is shorter than this

# ── Exclusion Rules (applied to job title) ─────────────────────────────────────
EXCLUDE_TITLE_PATTERNS = [
    r"\bsupply\s+chain\b",
    r"\bfp[&\s]+a\b|\bfinancial\s+planning\b|\bfinancial\s+analyst\b",
    r"\bmedical\s+affairs\b",
    r"\bdeposit\s+pricing\b|\btreasury\b",
    r"\bsales\s+operations\b",
    r"\bdata\s+engineer\b",   # IC data engineer; "principal analytics engineer" is safe — no "data engineer" substring
]

# ── Location Rules ─────────────────────────────────────────────────────────────
NEW_ENGLAND_NAMES = {
    "massachusetts", "connecticut", "rhode island", "new hampshire", "vermont", "maine",
    "boston", "cambridge", "somerville", "newton", "waltham", "lexington", "woburn",
    "worcester", "framingham", "quincy", "braintree", "natick", "norwood", "needham",
    "burlington", "andover", "providence", "hartford", "manchester", "nashua",
}
NEW_ENGLAND_ABBREVS = {"ma", "ct", "ri", "nh", "vt", "me"}

NON_NE_NAMES = {
    "alabama", "alaska", "arizona", "arkansas", "california", "colorado", "delaware",
    "florida", "georgia", "hawaii", "idaho", "illinois", "indiana", "iowa", "kansas",
    "kentucky", "louisiana", "maryland", "michigan", "minnesota", "mississippi",
    "missouri", "montana", "nebraska", "nevada", "new jersey", "new mexico", "new york",
    "north carolina", "north dakota", "ohio", "oklahoma", "oregon", "pennsylvania",
    "south carolina", "south dakota", "tennessee", "texas", "utah", "virginia",
    "washington", "west virginia", "wisconsin", "wyoming",
}
NON_NE_ABBREVS = {
    "al", "ak", "az", "ar", "ca", "co", "de", "fl", "ga", "hi", "id", "il", "in", "ia",
    "ks", "ky", "la", "md", "mi", "mn", "ms", "mo", "mt", "ne", "nv", "nj", "nm", "ny",
    "nc", "nd", "oh", "ok", "or", "pa", "sc", "sd", "tn", "tx", "ut", "va", "wa", "wv",
    "wi", "wy",
}

MIN_SALARY = 130_000

# ── Workday Career Page Configs ────────────────────────────────────────────────
# Tenant IDs are best-effort; each call fails silently if the tenant/site is wrong.
# Verify at: https://{tenant}.wd1.myworkdayjobs.com/en-US/{site}
WORKDAY_SITES = [
    {"name": "Wellington Management", "tenant": "wellington",    "site": "WellingtonCareers"},
    {"name": "State Street",          "tenant": "statestreet",   "site": "StateStreetCareers"},
    {"name": "MFS Investment Mgmt",   "tenant": "mfs",           "site": "MFSExternalCareerSite"},
    {"name": "Hologic",               "tenant": "hologic",       "site": "Hologic"},
    {"name": "Waters Corporation",    "tenant": "waters",        "site": "WatersExternalCareerSite"},
    {"name": "TJX Companies",         "tenant": "tjx",           "site": "TJX"},
    {"name": "Clean Harbors",         "tenant": "cleanharbors",  "site": "CleanHarbors"},
    {"name": "Staples",               "tenant": "staples",       "site": "StaplesExternalCareerSite"},
    {"name": "Planet Fitness",        "tenant": "planetfitness", "site": "PlanetFitnessCareers"},
]
WORKDAY_SEARCH_TERMS = ["analytics", "business intelligence", "data analyst"]

# ── Google Jobs queries (SerpAPI — optional, 100 free credits/month) ───────────
# Kept to 4 queries × 3 runs/week = ~48/month — stays within free tier
GOOGLE_QUERIES = [
    "Director of Analytics Boston MA",
    "Principal Data Analyst Boston remote",
    "Head of Analytics Massachusetts",
    "Senior Manager Business Intelligence Boston",
]

_WORKDAY_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
}


# ═══════════════════════════════════════════════════════════════════════════════
# Filtering
# ═══════════════════════════════════════════════════════════════════════════════

def is_title_match(title: str) -> bool:
    t = title.lower()
    return any(re.search(p, t) for p in TITLE_PATTERNS)


def is_excluded(title: str) -> bool:
    t = title.lower()
    return any(re.search(p, t) for p in EXCLUDE_TITLE_PATTERNS)


def count_keyword_groups(text: str) -> int:
    t = text.lower()
    return sum(1 for group in MUST_HAVE_GROUPS if any(kw in t for kw in group))


def passes_keyword_filter(job: dict) -> bool:
    desc = (job.get("description") or "").lower()
    title = (job.get("title") or "").lower()
    if len(desc) < SHORT_DESC_THRESHOLD:
        return True  # snippet too short to judge; title match is sufficient
    return count_keyword_groups(title + " " + desc) >= MUST_HAVE_MIN


def is_in_target_location(job: dict) -> bool:
    loc = (job.get("location", {}).get("display_name") or "").lower().strip()

    if not loc or "remote" in loc or "anywhere" in loc or loc in {"united states", "usa", "us"}:
        return True

    for name in NEW_ENGLAND_NAMES:
        if name in loc:
            return True
    for abbrev in NEW_ENGLAND_ABBREVS:
        if re.search(r"\b" + abbrev + r"\b", loc):
            return True

    for name in NON_NE_NAMES:
        if name in loc:
            return False
    for abbrev in NON_NE_ABBREVS:
        if re.search(r"\b" + abbrev + r"\b", loc):
            return False

    return True  # unresolved — accept


def salary_passes(job: dict) -> bool:
    lo = job.get("salary_min") or 0
    hi = job.get("salary_max") or 0
    if not lo and not hi:
        return True  # no salary data — don't penalise non-transparent postings
    if hi and hi < MIN_SALARY:
        return False
    # If only a floor is given and it's very low (<60% of our minimum), skip
    if lo and not hi and lo < MIN_SALARY * 0.6:
        return False
    return True


def is_relevant(job: dict) -> bool:
    title = job.get("title") or ""
    if not is_title_match(title):
        return False
    if is_excluded(title):
        return False
    if not passes_keyword_filter(job):
        return False
    if not salary_passes(job):
        return False
    return True


def job_fingerprint(job: dict) -> str:
    uid = job.get("id")
    if uid:
        return str(uid)
    return hashlib.md5(
        (
            (job.get("title") or "")
            + (job.get("company", {}).get("display_name") or "")
            + (job.get("location", {}).get("display_name") or "")
        ).encode()
    ).hexdigest()


def add_jobs(candidates: list, seen: set, jobs: list, label: str = "") -> int:
    raw, added = len(candidates), 0
    for job in candidates:
        fid = job_fingerprint(job)
        if fid not in seen and is_relevant(job) and is_in_target_location(job):
            seen.add(fid)
            jobs.append(job)
            added += 1
    if raw > 0 or label:
        print(f"  [{label}] raw={raw} kept={added}")
    return added


# ═══════════════════════════════════════════════════════════════════════════════
# Source: Adzuna API
# ═══════════════════════════════════════════════════════════════════════════════

def search_adzuna(query: str, location: str = None) -> list:
    params = {
        "app_id": ADZUNA_APP_ID,
        "app_key": ADZUNA_API_KEY,
        "results_per_page": 50,
        "what": query,
        "max_days_old": 3,
    }
    if location:
        params["where"] = location
        params["distance"] = 50
    try:
        resp = requests.get(f"{ADZUNA_BASE}/1", params=params, timeout=15)
        resp.raise_for_status()
        results = resp.json().get("results", [])
        for r in results:
            r.setdefault("_source", "Adzuna")
            r.setdefault("_salary_text", "")
        return results
    except Exception as e:
        print(f"  [WARN] Adzuna '{query}' loc='{location}': {e}")
        return []


# ═══════════════════════════════════════════════════════════════════════════════
# Source: Indeed RSS (free, no API key)
# ═══════════════════════════════════════════════════════════════════════════════

def search_indeed_rss(query: str, location: str = "Boston, MA") -> list:
    url = (
        "https://www.indeed.com/rss"
        f"?q={requests.utils.quote(query)}"
        f"&l={requests.utils.quote(location)}"
        "&radius=50"
        "&fromage=3"
        "&sort=date"
    )
    try:
        feed = feedparser.parse(url)
        jobs = []
        for entry in feed.entries:
            raw_title = entry.get("title", "")
            parts = [p.strip() for p in raw_title.split(" - ")]
            title   = parts[0] if parts else raw_title
            company = parts[1] if len(parts) > 1 else ""
            loc     = parts[2] if len(parts) > 2 else location
            jobs.append({
                "id": None,
                "title": title,
                "company": {"display_name": company},
                "location": {"display_name": loc},
                "redirect_url": entry.get("link", "#"),
                "created": (entry.get("published") or "")[:10],
                "salary_min": None,
                "salary_max": None,
                "_salary_text": "",
                "_source": "Indeed",
                "description": entry.get("summary", ""),
            })
        return jobs
    except Exception as e:
        print(f"  [WARN] Indeed RSS '{query}' loc='{location}': {e}")
        return []


# ═══════════════════════════════════════════════════════════════════════════════
# Source: Workday career pages
# ═══════════════════════════════════════════════════════════════════════════════

def search_workday(site_cfg: dict, term: str) -> list:
    tenant  = site_cfg["tenant"]
    site    = site_cfg["site"]
    company = site_cfg["name"]
    url     = f"https://{tenant}.wd1.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs"
    payload = {"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": term}
    try:
        resp = requests.post(url, json=payload, headers=_WORKDAY_HEADERS, timeout=15)
        resp.raise_for_status()
        postings = resp.json().get("jobPostings", [])
        jobs = []
        for p in postings:
            ext_path  = p.get("externalPath", "")
            apply_url = f"https://{tenant}.wd1.myworkdayjobs.com/en-US/{site}{ext_path}"
            jobs.append({
                "id": None,
                "title":   p.get("title", ""),
                "company": {"display_name": company},
                "location": {"display_name": p.get("locationsText", "")},
                "redirect_url": apply_url,
                "created": p.get("postedOn", ""),
                "salary_min": None,
                "salary_max": None,
                "_salary_text": "",
                "_source": f"Workday/{company}",
                "description": " ".join(p.get("bulletFields") or []),
            })
        return jobs
    except Exception as e:
        print(f"  [SKIP] Workday {company}/{term}: {e}")
        return []


# ═══════════════════════════════════════════════════════════════════════════════
# Source: Google Jobs via SerpAPI (optional)
# ═══════════════════════════════════════════════════════════════════════════════

def search_google_jobs(query: str) -> list:
    if not SERPAPI_KEY:
        return []
    params = {
        "engine":   "google_jobs",
        "q":        query,
        "api_key":  SERPAPI_KEY,
        "chips":    "date_posted:3days",
        "location": "Boston, Massachusetts, United States",
    }
    try:
        resp = requests.get("https://serpapi.com/search.json", params=params, timeout=15)
        resp.raise_for_status()
        return resp.json().get("jobs_results", [])
    except Exception as e:
        print(f"  [WARN] Google Jobs '{query}': {e}")
        return []


def _normalize_google_job(raw: dict) -> dict:
    apply_options = raw.get("apply_options") or []
    url           = apply_options[0].get("link", "#") if apply_options else "#"
    extensions    = raw.get("detected_extensions") or {}
    return {
        "id":       None,
        "title":    raw.get("title", ""),
        "company":  {"display_name": raw.get("company_name", "")},
        "location": {"display_name": raw.get("location", "")},
        "redirect_url": url,
        "created":  extensions.get("posted_at", ""),
        "salary_min":   None,
        "salary_max":   None,
        "_salary_text": extensions.get("salary", ""),
        "_source":  "Google Jobs",
        "description":  raw.get("description", ""),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# Collection orchestrator
# ═══════════════════════════════════════════════════════════════════════════════

def collect_all_jobs() -> list:
    seen: set  = set()
    jobs: list = []

    print("── Adzuna (location-targeted) ──")
    for loc in ADZUNA_LOCATIONS:
        for q in ADZUNA_QUERIES:
            add_jobs(search_adzuna(q, loc), seen, jobs, f"Adzuna/{loc[:5]}/{q[:22]}")
            time.sleep(0.3)

    print("── Adzuna (USA-wide + remote) ──")
    for q in ADZUNA_QUERIES:
        add_jobs(search_adzuna(q), seen, jobs, f"Adzuna/US/{q[:22]}")
        time.sleep(0.3)

    print("── Indeed RSS (Boston, MA) ──")
    for q in INDEED_QUERIES:
        add_jobs(search_indeed_rss(q, "Boston, MA"), seen, jobs, f"Indeed/Boston/{q[:22]}")
        time.sleep(0.5)

    print("── Indeed RSS (Massachusetts) ──")
    for q in INDEED_QUERIES:
        add_jobs(search_indeed_rss(q, "Massachusetts"), seen, jobs, f"Indeed/MA/{q[:22]}")
        time.sleep(0.5)

    print("── Indeed RSS (Remote USA) ──")
    for q in INDEED_QUERIES:
        add_jobs(search_indeed_rss(q, "remote"), seen, jobs, f"Indeed/Remote/{q[:22]}")
        time.sleep(0.5)

    print("── Company-targeted searches (Indeed) ──")
    for term, company in COMPANY_QUERIES:
        full_q = f"{term} {company}"
        add_jobs(search_indeed_rss(full_q, "Boston, MA"), seen, jobs, f"Indeed/Co/{company[:12]}")
        add_jobs(search_indeed_rss(full_q, "remote"),     seen, jobs, f"Indeed/CoR/{company[:12]}")
        time.sleep(0.5)

    print("── Workday career pages ──")
    for site in WORKDAY_SITES:
        for term in WORKDAY_SEARCH_TERMS:
            add_jobs(search_workday(site, term), seen, jobs, f"WD/{site['name'][:12]}/{term[:10]}")
            time.sleep(0.5)

    if SERPAPI_KEY:
        print("── Google Jobs (SerpAPI) ──")
        for q in GOOGLE_QUERIES:
            raw = search_google_jobs(q)
            add_jobs([_normalize_google_job(r) for r in raw], seen, jobs, f"Google/{q[:25]}")
            time.sleep(1.0)
    else:
        print("SERPAPI_KEY not set — skipping Google Jobs.")

    return jobs


# ═══════════════════════════════════════════════════════════════════════════════
# Email builder
# ═══════════════════════════════════════════════════════════════════════════════

def _match_badge(job: dict) -> str:
    desc  = (job.get("description") or "").lower()
    title = (job.get("title") or "").lower()
    n     = count_keyword_groups(title + " " + desc)
    if n >= 4:
        return (
            '<span style="background:#1e7e34;color:#fff;padding:2px 8px;'
            'border-radius:3px;font-size:11px;vertical-align:middle;">'
            "&#9733; Strong Match</span>"
        )
    if n >= 2:
        return (
            '<span style="background:#0069d9;color:#fff;padding:2px 8px;'
            'border-radius:3px;font-size:11px;vertical-align:middle;">'
            "&#10003; Match</span>"
        )
    return ""


def _location_tag(job: dict) -> str:
    loc = (job.get("location", {}).get("display_name") or "").lower()
    if "boston" in loc:
        return '<span style="color:#c62828;font-weight:bold;">Boston</span>'
    if "remote" in loc or not loc or loc in {"united states", "usa"}:
        return '<span style="color:#6a1b9a;">Remote</span>'
    return ""


def build_html(jobs: list) -> str:
    now   = datetime.now(timezone.utc)
    count = len(jobs)

    source_counts: dict = {}
    for job in jobs:
        src = (job.get("_source") or "Adzuna").split("/")[0]
        source_counts[src] = source_counts.get(src, 0) + 1
    src_summary = " &bull; ".join(
        f"{s}: {n}" for s, n in sorted(source_counts.items())
    )

    html = f"""<!DOCTYPE html>
<html>
<body style="font-family:Arial,sans-serif;max-width:860px;margin:auto;padding:24px;color:#222;">
<h2 style="color:#1a73e8;margin-bottom:4px;">&#128202; Data &amp; Analytics Leadership — Job Digest</h2>
<p style="color:#666;margin-top:4px;font-size:13px;">
  {now.strftime('%B %d, %Y &mdash; %I:%M %p UTC')}
  &nbsp;&bull;&nbsp; Last 3 days
  &nbsp;&bull;&nbsp; Boston / Massachusetts / Remote USA
</p>

<table style="width:100%;border:1px solid #ddd;border-radius:6px;background:#f8f9fa;
              padding:12px 16px;border-spacing:0;font-size:13px;">
  <tr>
    <td style="padding:4px 0;"><b>Target Titles:</b>
      Director &bull; Sr. Manager &bull; Principal &bull; Head &bull; Lead &mdash; Analytics / BI
    </td>
  </tr>
  <tr>
    <td style="padding:4px 0;"><b>Industries:</b>
      Financial Services &bull; Healthcare / Life Sciences &bull; Higher Ed
      &bull; MarTech/SaaS &bull; Retail &bull; Biotech &bull; Environmental
    </td>
  </tr>
  <tr>
    <td style="padding:4px 0;"><b>Keywords (≥2 required):</b>
      Snowflake &bull; Power BI / Tableau &bull; SQL
      &bull; Analytics Strategy &bull; People Management
    </td>
  </tr>
  <tr>
    <td style="padding:4px 0;"><b>Min Salary:</b> $130,000 (when posted)</td>
  </tr>
</table>

<hr style="border:1px solid #eee;margin:16px 0;">
<h3 style="color:#333;margin-bottom:4px;">{count} job{'s' if count != 1 else ''} found</h3>
{f'<p style="color:#999;font-size:12px;">By source: {src_summary}</p>' if src_summary else ''}
"""

    if not jobs:
        html += (
            "<p style='color:#888;'>No matching jobs found in this run — the agent is working correctly. "
            "Check back tomorrow or trigger a manual run.</p>"
        )
    else:
        for i, job in enumerate(jobs, 1):
            title    = job.get("title") or "N/A"
            company  = (job.get("company") or {}).get("display_name") or "N/A"
            location = (job.get("location") or {}).get("display_name") or "N/A"
            url      = job.get("redirect_url") or "#"
            created  = (job.get("created") or "")[:10]
            source   = job.get("_source") or "Adzuna"
            badge    = _match_badge(job)
            loc_tag  = _location_tag(job)

            lo, hi = job.get("salary_min"), job.get("salary_max")
            salary = ""
            if lo and hi:
                salary = f"${lo:,.0f}–${hi:,.0f}/yr"
            elif lo:
                salary = f"From ${lo:,.0f}/yr"
            elif hi:
                salary = f"Up to ${hi:,.0f}/yr"
            elif job.get("_salary_text"):
                salary = job["_salary_text"]

            salary_style = "font-size:14px;"
            if lo and lo >= 150_000:
                salary_style += "color:#1e7e34;font-weight:bold;"
            elif lo and lo >= 130_000:
                salary_style += "color:#0069d9;"

            html += f"""
<div style="margin-bottom:18px;padding:16px;border:1px solid #e0e0e0;
            border-radius:6px;background:#fafafa;">
  <h3 style="margin:0 0 6px;font-size:16px;">
    <a href="{url}" style="color:#1a73e8;text-decoration:none;">{i}. {title}</a>
    &nbsp;{badge}
  </h3>
  <p style="margin:3px 0;font-size:14px;"><b>Company:</b> {company}</p>
  <p style="margin:3px 0;font-size:14px;">
    <b>Location:</b> {location}&nbsp;{loc_tag}
  </p>"""

            if salary:
                html += f"""
  <p style="margin:3px 0;{salary_style}"><b>Salary:</b> {salary}</p>"""

            if created:
                html += f"""
  <p style="margin:3px 0;font-size:13px;color:#888;">Posted: {created}</p>"""

            html += f"""
  <p style="margin:3px 0;font-size:12px;color:#bbb;">Source: {source}</p>
  <a href="{url}" style="display:inline-block;margin-top:8px;background:#1a73e8;
     color:#fff;padding:7px 16px;border-radius:4px;text-decoration:none;
     font-size:13px;">View &amp; Apply &#8594;</a>
</div>"""

    html += (
        "\n<hr style='border:1px solid #eee;margin-top:30px;'>"
        "<p style='color:#bbb;font-size:12px;'>"
        "Sources: Adzuna API &bull; Indeed RSS &bull; Workday Career Pages &bull; "
        "Automated via GitHub Actions"
        "</p>\n</body></html>"
    )
    return html


def send_email(jobs: list):
    now     = datetime.now(timezone.utc)
    subject = (
        f"[Analytics Jobs] {len(jobs)} Director/Principal Roles "
        f"— {now.strftime('%b %d, %Y')}"
    )
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = GMAIL_USER
    msg["To"]      = ", ".join(RECIPIENT_EMAILS)
    msg.attach(MIMEText(build_html(jobs), "html"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(GMAIL_USER, GMAIL_APP_PASSWORD)
        server.sendmail(GMAIL_USER, RECIPIENT_EMAILS, msg.as_string())
    print(f"Email sent: {subject}")


# ═══════════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    print("Starting Data & Analytics Leadership job search...")
    jobs = collect_all_jobs()
    print(f"\nTotal: {len(jobs)} jobs after deduplication and all filters.")
    send_email(jobs)
    print("Done.")
