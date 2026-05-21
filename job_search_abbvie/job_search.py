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

ADZUNA_APP_ID = os.environ["ADZUNA_APP_ID"]
ADZUNA_API_KEY = os.environ["ADZUNA_API_KEY"]
GMAIL_USER = os.environ["GMAIL_USER"]
GMAIL_APP_PASSWORD = os.environ["GMAIL_APP_PASSWORD"]
RECIPIENT_EMAILS = [e.strip() for e in os.environ.get("RECIPIENT_EMAIL", GMAIL_USER).split(",")]
SERPAPI_KEY = os.environ.get("SERPAPI_KEY", "")

ADZUNA_BASE_URL = "https://api.adzuna.com/v1/api/jobs/us/search"

# Company-specific: AbbVie only
ABBVIE_NAMES = {"abbvie", "abbvie inc"}

# Location: Massachusetts only
TARGET_STATE_NAMES = {"massachusetts"}
TARGET_STATE_ABBREVS = {"MA"}

NON_TARGET_STATES = {
    "alabama", "alaska", "arizona", "arkansas", "california", "colorado",
    "delaware", "florida", "georgia", "hawaii", "idaho", "illinois",
    "indiana", "iowa", "kansas", "kentucky", "louisiana", "maine",
    "maryland", "michigan", "minnesota", "mississippi", "missouri",
    "montana", "nebraska", "nevada", "new hampshire", "new jersey",
    "new mexico", "new york", "north carolina", "north dakota", "ohio",
    "oklahoma", "oregon", "pennsylvania", "rhode island", "south carolina",
    "south dakota", "tennessee", "texas", "utah", "vermont", "virginia",
    "washington", "west virginia", "wisconsin", "wyoming", "connecticut",
}

NON_TARGET_ABBREVS = {
    "al", "ak", "az", "ar", "ca", "co", "ct", "de", "fl", "ga", "hi", "id",
    "il", "in", "ia", "ks", "ky", "la", "me", "md", "mi", "mn", "ms",
    "mo", "mt", "ne", "nv", "nh", "nj", "nm", "ny", "nc", "nd", "oh",
    "ok", "or", "pa", "ri", "sc", "sd", "tn", "tx", "ut", "vt", "va",
    "wa", "wv", "wi", "wy",
}

# Adzuna queries: include "AbbVie" to bias results; broad validation terms to
# catch all role types. Company filter still applied after collection.
ADZUNA_QUERIES = [
    "AbbVie validation engineer",
    "AbbVie validation associate",
    "AbbVie validation specialist",
    "AbbVie equipment validation",
    "AbbVie process validation",
    "AbbVie GMP validation",
    "AbbVie qualification engineer",
    "AbbVie IQ OQ PQ",
    "AbbVie quality engineer",
    "AbbVie quality assurance",
    "AbbVie quality associate",
    "AbbVie quality specialist",
    "AbbVie quality control",
    "AbbVie QA QC",
]

INDEED_RSS_QUERIES = [
    "AbbVie validation engineer",
    "AbbVie validation associate",
    "AbbVie equipment validation GMP",
    "AbbVie process validation pharmaceutical",
    "AbbVie IQ OQ PQ qualification",
    "AbbVie quality engineer",
    "AbbVie quality assurance pharmaceutical",
    "AbbVie quality associate GMP",
    "AbbVie quality control",
    "AbbVie QA QC",
]

BIOSPACE_QUERIES = [
    "AbbVie validation engineer",
    "AbbVie validation associate",
    "AbbVie validation specialist",
    "AbbVie equipment qualification",
    "AbbVie process validation",
    "AbbVie quality engineer",
    "AbbVie quality assurance",
    "AbbVie quality specialist",
    "AbbVie quality control",
    "AbbVie QA QC",
]

GOOGLE_SEARCH_QUERIES = [
    "AbbVie validation engineer Massachusetts",
    "AbbVie validation associate Massachusetts",
    "AbbVie quality engineer Massachusetts",
    "AbbVie quality assurance Massachusetts",
]

EXCLUDE_TITLE_KEYWORDS = [
    "computer system validation", "csv validation", "it validation",
    "software validation", "computer systems validation",
]

EXCLUDE_SENIORITY = [
    "senior", "sr.", "sr ", "lead", "manager", "director",
    "principal", "head of", "vp ", "vice president", "staff engineer",
]


# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------

def is_abbvie(job):
    """Return True only if the job is posted by AbbVie or an AbbVie subsidiary."""
    company = (job.get("company", {}).get("display_name") or "").lower().strip()
    return any(name in company for name in ABBVIE_NAMES)


def is_in_target_state(job):
    """Return True if job is in Massachusetts, remote, or unspecified."""
    loc = (job.get("location", {}).get("display_name") or "").lower().strip()
    desc = (job.get("description") or "").lower()

    if not loc or "remote" in loc or "anywhere" in loc:
        return True

    for name in TARGET_STATE_NAMES:
        if name in loc:
            return True
    for abbrev in TARGET_STATE_ABBREVS:
        if re.search(r'\b' + abbrev.lower() + r'\b', loc):
            return True

    for state in NON_TARGET_STATES:
        if state in loc:
            return False
    for abbrev in NON_TARGET_ABBREVS:
        if re.search(r'\b' + abbrev + r'\b', loc):
            return False

    # Generic / city-only location: check description for MA mentions
    for name in TARGET_STATE_NAMES:
        if name in desc:
            return True
    for abbrev in TARGET_STATE_ABBREVS:
        if re.search(r'\b' + abbrev.lower() + r'\b', desc):
            return True

    return True


def is_relevant(job):
    title = (job.get("title") or "").lower()
    for kw in EXCLUDE_TITLE_KEYWORDS:
        if kw in title:
            return False
    for kw in EXCLUDE_SENIORITY:
        if kw in title:
            return False
    # Phrase/substring terms — safe to match without word boundaries
    phrase_terms = [
        "validation", "qualification", "iq/oq", "iq oq", "gmp", "gxp", "qualify",
        "quality engineer", "quality assurance", "quality associate",
        "quality specialist", "quality control", "qa/qc",
    ]
    if any(t in title for t in phrase_terms):
        return True
    # "qa" / "qc" need word boundaries — avoid matching "aqua", "squad", etc.
    return bool(re.search(r'\bqa\b', title) or re.search(r'\bqc\b', title))


# ---------------------------------------------------------------------------
# AbbVie Direct (Phenom career site — https://careers.abbvie.com)
# ---------------------------------------------------------------------------
# AbbVie uses Phenom, not Workday. The site is server-side rendered;
# job URLs encode city+state: /en/job/[title]-in-[city]-[state]-jid-[id]
# so we parse state directly from the href without needing an API key.

ABBVIE_PHENOM_BASE = "https://careers.abbvie.com"

ABBVIE_DIRECT_QUERIES = [
    "validation engineer",
    "validation associate",
    "validation specialist",
    "equipment validation",
    "process validation",
    "GMP validation",
    "qualification engineer",
    "IQ OQ PQ",
    "quality engineer",
    "quality assurance",
    "quality associate",
    "quality specialist",
    "quality control",
]


def search_abbvie_phenom(search_text):
    """Scrape AbbVie Phenom careers page and parse job links from HTML."""
    url = (
        f"{ABBVIE_PHENOM_BASE}/en/jobs/"
        f"?q={requests.utils.quote(search_text)}&pagesize=50"
    )
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml",
    }
    try:
        resp = requests.get(url, headers=headers, timeout=15)
        resp.raise_for_status()
        html = resp.text

        jobs = []
        seen_jids = set()

        # Each job link: /en/job/[title-slug]-in-[city-slug]-[state]-jid-[id]
        for href in re.findall(r'href=["\']?(/en/job/[^"\'>\s]+)["\']?', html):
            m = re.match(r'/en/job/(.*)-jid-(\d+)', href)
            if not m:
                continue
            slug_body, jid = m.group(1), m.group(2)
            if jid in seen_jids:
                continue
            seen_jids.add(jid)

            # Strip trailing 2-char code: "...-in-cambridge-ma" → state="MA"
            m2 = re.match(r'^(.*)-([a-z]{2})$', slug_body)
            if not m2:
                continue
            title_city, state_abbrev = m2.group(1), m2.group(2).upper()

            # Skip non-US / non-MA jobs — international URLs use country/county
            # codes that are not US state abbreviations (e.g. "SO" for Sligo, Ireland)
            if state_abbrev not in TARGET_STATE_ABBREVS:
                continue

            # Split on last "-in-" to separate title from city
            in_idx = title_city.rfind('-in-')
            if in_idx >= 0:
                title = title_city[:in_idx].replace('-', ' ').title()
                city  = title_city[in_idx + 4:].replace('-', ' ').title()
            else:
                title = title_city.replace('-', ' ').title()
                city  = ""

            state_full = {
                "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas",
                "CA": "California", "CO": "Colorado", "CT": "Connecticut",
                "DE": "Delaware", "FL": "Florida", "GA": "Georgia", "HI": "Hawaii",
                "ID": "Idaho", "IL": "Illinois", "IN": "Indiana", "IA": "Iowa",
                "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine",
                "MD": "Maryland", "MA": "Massachusetts", "MI": "Michigan",
                "MN": "Minnesota", "MS": "Mississippi", "MO": "Missouri",
                "MT": "Montana", "NE": "Nebraska", "NV": "Nevada",
                "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico",
                "NY": "New York", "NC": "North Carolina", "ND": "North Dakota",
                "OH": "Ohio", "OK": "Oklahoma", "OR": "Oregon",
                "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina",
                "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas",
                "UT": "Utah", "VT": "Vermont", "VA": "Virginia", "WA": "Washington",
                "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming",
            }.get(state_abbrev, state_abbrev)
            location_display = f"{city}, {state_full}" if city else state_full

            jobs.append({
                "id": jid,
                "title": title,
                "company": {"display_name": "AbbVie"},
                "location": {"display_name": location_display},
                "redirect_url": f"{ABBVIE_PHENOM_BASE}{href}",
                "created": "",
                "salary_min": None,
                "salary_max": None,
                "_salary_text": "",
                "_source": "AbbVie Careers",
                "description": "",
            })

        return jobs
    except Exception as e:
        print(f"  [WARN] AbbVie Phenom '{search_text}': {e}")
        return []


# ---------------------------------------------------------------------------
# Adzuna
# ---------------------------------------------------------------------------

def search_adzuna(query, location=None, page=1):
    params = {
        "app_id": ADZUNA_APP_ID,
        "app_key": ADZUNA_API_KEY,
        "results_per_page": 50,
        "what": query,
        "max_days_old": 7,
    }
    if location:
        params["where"] = location
        params["distance"] = 50

    url = f"{ADZUNA_BASE_URL}/{page}"
    try:
        resp = requests.get(url, params=params, timeout=15)
        resp.raise_for_status()
        return resp.json().get("results", [])
    except Exception as e:
        print(f"  [WARN] Adzuna '{query}' in '{location or 'USA'}': {e}")
        return []


# ---------------------------------------------------------------------------
# Indeed RSS
# ---------------------------------------------------------------------------

def search_indeed_rss(query, location):
    url = (
        "https://www.indeed.com/rss"
        f"?q={requests.utils.quote(query)}"
        f"&l={requests.utils.quote(location)}"
        "&radius=50"
        "&fromage=7"
        "&sort=date"
    )
    try:
        feed = feedparser.parse(url)
        jobs = []
        for entry in feed.entries:
            raw_title = entry.get("title", "")
            parts = [p.strip() for p in raw_title.split(" - ")]
            title = parts[0] if parts else raw_title
            company = parts[1] if len(parts) > 1 else ""
            loc_hint = parts[2] if len(parts) > 2 else location
            jobs.append({
                "id": None,
                "title": title,
                "company": {"display_name": company},
                "location": {"display_name": loc_hint},
                "redirect_url": entry.get("link", "#"),
                "created": entry.get("published", "")[:10],
                "salary_min": None,
                "salary_max": None,
                "_salary_text": "",
                "_source": "Indeed",
                "description": entry.get("summary", ""),
            })
        return jobs
    except Exception as e:
        print(f"  [WARN] Indeed RSS '{query}' in '{location}': {e}")
        return []


# ---------------------------------------------------------------------------
# BioSpace RSS
# ---------------------------------------------------------------------------

def search_biospace_rss(query, location):
    url = (
        "https://www.biospace.com/jobs/rss"
        f"?keywords={requests.utils.quote(query)}"
        f"&location={requests.utils.quote(location)}"
    )
    try:
        feed = feedparser.parse(url)
        jobs = []
        for entry in feed.entries:
            raw_title = entry.get("title", "")
            parts = [p.strip() for p in raw_title.split(" - ")]
            title = parts[0] if parts else raw_title
            company = parts[1] if len(parts) > 1 else ""
            loc_hint = parts[2] if len(parts) > 2 else location
            jobs.append({
                "id": None,
                "title": title,
                "company": {"display_name": company},
                "location": {"display_name": loc_hint},
                "redirect_url": entry.get("link", "#"),
                "created": entry.get("published", "")[:10],
                "salary_min": None,
                "salary_max": None,
                "_salary_text": "",
                "_source": "BioSpace",
                "description": entry.get("summary", ""),
            })
        return jobs
    except Exception as e:
        print(f"  [WARN] BioSpace RSS '{query}' in '{location}': {e}")
        return []


# ---------------------------------------------------------------------------
# Google Jobs via SerpAPI
# ---------------------------------------------------------------------------

def search_google_jobs(query):
    if not SERPAPI_KEY:
        return []
    params = {
        "engine": "google_jobs",
        "q": query,
        "api_key": SERPAPI_KEY,
        "chips": "date_posted:month",
    }
    try:
        resp = requests.get("https://serpapi.com/search.json", params=params, timeout=15)
        resp.raise_for_status()
        return resp.json().get("jobs_results", [])
    except Exception as e:
        print(f"  [WARN] Google Jobs '{query}': {e}")
        return []


def normalize_google_job(raw):
    apply_options = raw.get("apply_options") or []
    url = apply_options[0].get("link", "#") if apply_options else "#"
    extensions = raw.get("detected_extensions") or {}
    return {
        "id": None,
        "title": raw.get("title", ""),
        "company": {"display_name": raw.get("company_name", "")},
        "location": {"display_name": raw.get("location", "")},
        "redirect_url": url,
        "created": extensions.get("posted_at", ""),
        "salary_min": None,
        "salary_max": None,
        "_salary_text": extensions.get("salary", ""),
        "_source": "Google Jobs",
        "description": raw.get("description", ""),
    }


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def job_fingerprint(job):
    return job.get("id") or hashlib.md5(
        f"{job.get('title', '')}"
        f"{job.get('company', {}).get('display_name', '')}"
        f"{job.get('location', {}).get('display_name', '')}".encode()
    ).hexdigest()


def add_jobs(candidates, seen, jobs, label=""):
    raw = len(candidates)
    added = 0
    for job in candidates:
        fid = job_fingerprint(job)
        if fid not in seen:
            if is_relevant(job) and is_in_target_state(job) and is_abbvie(job):
                seen.add(fid)
                jobs.append(job)
                added += 1
    if raw > 0 or label:
        print(f"  [{label}] raw={raw} kept={added}")
    return added


# ---------------------------------------------------------------------------
# Main collection
# ---------------------------------------------------------------------------

def collect_all_jobs():
    seen = set()
    jobs = []

    # --- AbbVie Careers direct (Phenom) ---
    print("Searching AbbVie Careers (Phenom direct)...")
    for query in ABBVIE_DIRECT_QUERIES:
        candidates = search_abbvie_phenom(query)
        add_jobs(candidates, seen, jobs, label=f"AbbVieDirect/{query[:20]}")
        time.sleep(1.0)

    # --- Adzuna: Massachusetts + USA-wide ---
    print("Searching Adzuna (Massachusetts)...")
    for query in ADZUNA_QUERIES:
        add_jobs(search_adzuna(query, location="Massachusetts"), seen, jobs,
                 label=f"Adzuna/MA/{query[:20]}")
        time.sleep(0.3)

    print("Searching Adzuna (USA-wide)...")
    for query in ADZUNA_QUERIES:
        add_jobs(search_adzuna(query), seen, jobs,
                 label=f"Adzuna/US/{query[:20]}")
        time.sleep(0.3)

    # --- Indeed RSS ---
    print("Searching Indeed RSS (Massachusetts)...")
    for query in INDEED_RSS_QUERIES:
        add_jobs(search_indeed_rss(query, "Massachusetts"), seen, jobs,
                 label=f"Indeed/MA/{query[:20]}")
        time.sleep(0.5)

    # --- BioSpace RSS ---
    print("Searching BioSpace RSS (Massachusetts)...")
    for query in BIOSPACE_QUERIES:
        add_jobs(search_biospace_rss(query, "Massachusetts"), seen, jobs,
                 label=f"BioSpace/MA/{query[:20]}")
        time.sleep(0.5)

    # --- Google Jobs (SerpAPI) ---
    if SERPAPI_KEY:
        print("Searching Google Jobs (SerpAPI)...")
        for query in GOOGLE_SEARCH_QUERIES:
            raw_results = search_google_jobs(query)
            add_jobs([normalize_google_job(r) for r in raw_results], seen, jobs,
                     label=f"Google/{query[:30]}")
            time.sleep(0.5)
    else:
        print("SERPAPI_KEY not set — skipping Google Jobs search.")

    return jobs


# ---------------------------------------------------------------------------
# Email
# ---------------------------------------------------------------------------

def build_html(jobs):
    now = datetime.now(timezone.utc)
    count = len(jobs)

    source_counts = {}
    for job in jobs:
        src = job.get("_source", "AbbVie Careers")
        source_counts[src] = source_counts.get(src, 0) + 1
    source_summary = " &bull; ".join(f"{src}: {n}" for src, n in sorted(source_counts.items()))

    html = f"""<!DOCTYPE html>
<html>
<body style="font-family:Arial,sans-serif;max-width:800px;margin:auto;padding:20px;color:#333;">
<h2 style="color:#071d49;">AbbVie Validation Jobs — Massachusetts</h2>
<p style="color:#666;">{now.strftime('%B %d, %Y — %I:%M %p UTC')} &nbsp;|&nbsp; Last 7 days</p>
<p><b>Company:</b> AbbVie (incl. Allergan / AbbVie subsidiaries)</p>
<p><b>Roles:</b> Validation Engineer &bull; Validation Associate &bull; Equipment Validation &bull; Process Validation &bull; GMP Qualification &bull; IQ/OQ/PQ &bull; Quality Engineer &bull; QA/QC Associate &bull; Quality Specialist</p>
<p><b>Location:</b> Massachusetts &nbsp;&bull;&nbsp; <b>Level:</b> Entry–Mid</p>
<hr style="border:1px solid #eee;">
<h3 style="color:#333;">{count} job{'s' if count != 1 else ''} found</h3>
{f'<p style="color:#888;font-size:13px;">By source: {source_summary}</p>' if source_summary else ''}
"""

    if not jobs:
        html += "<p style='color:#888;'>No new AbbVie validation jobs found in Massachusetts. Check back at the next run.</p>"
    else:
        for i, job in enumerate(jobs, 1):
            title = job.get("title", "N/A")
            company = job.get("company", {}).get("display_name", "N/A")
            location = job.get("location", {}).get("display_name", "N/A")
            url = job.get("redirect_url", "#")
            created = (job.get("created") or "")[:10]
            source = job.get("_source", "AbbVie Careers")

            salary = ""
            lo = job.get("salary_min")
            hi = job.get("salary_max")
            if lo and hi:
                salary = f"${lo:,.0f} – ${hi:,.0f} / yr"
            elif lo:
                salary = f"From ${lo:,.0f} / yr"
            elif job.get("_salary_text"):
                salary = job["_salary_text"]

            html += f"""
<div style="margin-bottom:18px;padding:16px;border:1px solid #e0e0e0;border-radius:6px;background:#fafafa;">
  <h3 style="margin:0 0 6px 0;font-size:16px;">
    <a href="{url}" style="color:#071d49;text-decoration:none;">{i}. {title}</a>
  </h3>
  <p style="margin:3px 0;font-size:14px;"><b>Company:</b> {company}</p>
  <p style="margin:3px 0;font-size:14px;"><b>Location:</b> {location}</p>
  {"<p style='margin:3px 0;font-size:14px;'><b>Salary:</b> " + salary + "</p>" if salary else ""}
  {"<p style='margin:3px 0;font-size:14px;color:#888;'>Posted: " + created + "</p>" if created else ""}
  <p style="margin:3px 0;font-size:12px;color:#aaa;">Source: {source}</p>
  <a href="{url}" style="display:inline-block;margin-top:8px;background:#071d49;color:white;padding:7px 16px;border-radius:4px;text-decoration:none;font-size:13px;">View &amp; Apply</a>
</div>"""

    html += "\n<hr style='border:1px solid #eee;margin-top:30px;'>"
    html += "<p style='color:#aaa;font-size:12px;'>Sources: AbbVie Careers &bull; Adzuna &bull; Indeed &bull; BioSpace &bull; Google Jobs &bull; Automated via GitHub Actions</p>"
    html += "\n</body></html>"
    return html


def send_email(jobs):
    now = datetime.now(timezone.utc)
    subject = f"[AbbVie Jobs] {len(jobs)} Validation/Quality Role{'s' if len(jobs) != 1 else ''} in MA — {now.strftime('%b %d, %I:%M %p UTC')}"

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = GMAIL_USER
    msg["To"] = ", ".join(RECIPIENT_EMAILS)
    msg.attach(MIMEText(build_html(jobs), "html"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(GMAIL_USER, GMAIL_APP_PASSWORD)
        server.sendmail(GMAIL_USER, RECIPIENT_EMAILS, msg.as_string())

    print(f"Email sent: {subject}")


if __name__ == "__main__":
    print("Starting AbbVie validation job search (Massachusetts)...")
    jobs = collect_all_jobs()
    print(f"Found {len(jobs)} AbbVie validation jobs in Massachusetts after filtering.")
    send_email(jobs)
    print("Done.")
