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

BASE_URL = "https://api.adzuna.com/v1/api/jobs/us/search"

SEARCH_QUERIES = [
    "validation engineer",
    "validation associate",
    "validation analyst",
    "validation specialist",
    "equipment validation",
    "equipment qualification",
    "process validation",
    "GMP validation",
    "IQ OQ PQ",
    "qualification engineer",
    "qualification specialist",
    "quality assurance validation",
]

LOCATIONS = [
    "Massachusetts",
    "New Hampshire",
    "Rhode Island",
    "Connecticut",
]

GOOGLE_SEARCH_QUERIES = [
    "pharma validation engineer OR associate OR specialist",
]

INDUSTRY_KEYWORDS = [
    "pharma", "pharmaceutical", "biopharma", "biopharmaceutical",
    "biotech", "biotechnology", "life sciences", "life science",
    "cro", "cmo", "medical device", "fda", "gmp", "gxp", "cgmp",
    "drug", "clinical", "biologics",
]

EXCLUDE_TITLE_KEYWORDS = [
    "computer system validation", "csv validation", "it validation",
    "software validation", "computer systems validation",
]

EXCLUDE_SENIORITY = [
    "senior", "sr.", "sr ", "lead", "manager", "director",
    "principal", "head of", "vp ", "vice president", "staff engineer",
]

TARGET_STATE_ABBREVS = {"MA", "NH", "RI", "CT"}
TARGET_STATE_NAMES = {
    "massachusetts", "new hampshire", "rhode island", "connecticut",
}

NON_TARGET_STATES = {
    "alabama", "alaska", "arizona", "arkansas", "california", "colorado",
    "delaware", "florida", "georgia", "hawaii", "idaho", "illinois",
    "indiana", "iowa", "kansas", "kentucky", "louisiana", "maine",
    "maryland", "michigan", "minnesota", "mississippi", "missouri",
    "montana", "nebraska", "nevada", "new jersey", "new mexico",
    "new york", "north carolina", "north dakota", "ohio", "oklahoma",
    "oregon", "pennsylvania", "south carolina", "south dakota",
    "tennessee", "texas", "utah", "vermont", "virginia", "washington",
    "west virginia", "wisconsin", "wyoming",
}

# Only unambiguous abbreviations — common English words like "or", "in",
# "me", "hi", "ok", "co", "de", "la" are excluded to prevent false rejections
# when scanning description text.  These are checked against the location
# field only (short structured string), so false positives are not a concern.
NON_TARGET_ABBREVS = {
    "al", "ak", "az", "ar", "ca", "fl", "ga", "id",
    "il", "ia", "ks", "ky", "md", "mi", "mn", "ms",
    "mo", "mt", "ne", "nv", "nj", "nm", "ny", "nc", "nd", "oh",
    "pa", "sc", "sd", "tn", "tx", "ut", "vt", "va", "wa", "wv",
    "wi", "wy",
}


def is_in_target_state(job):
    """Return True if job location is in a target state, remote, or unspecified."""
    loc = (job.get("location", {}).get("display_name") or "").lower().strip()
    desc = (job.get("description") or "").lower()

    # Remote or no location — always accept (valid for target-state residents)
    if not loc or "remote" in loc or "anywhere" in loc:
        return True

    # --- Check location field only (never description for rejection) ---
    # Pharma job descriptions routinely mention non-target states in context
    # (NJ/PA headquarters, FDA in Maryland, NY offices) — scanning them causes
    # nearly every legitimate MA/CT/RI/NH job to be falsely rejected.

    # Accept immediately if target state found in location string
    for name in TARGET_STATE_NAMES:
        if name in loc:
            return True
    for abbrev in TARGET_STATE_ABBREVS:
        if re.search(r'\b' + abbrev.lower() + r'\b', loc):
            return True

    # Reject if a non-target state is explicitly in the location string
    for state in NON_TARGET_STATES:
        if state in loc:
            return False
    for abbrev in NON_TARGET_ABBREVS:
        if re.search(r'\b' + abbrev + r'\b', loc):
            return False

    # Location is generic ("united states", city-only, etc.) — no state info found.
    # Scan description for TARGET state mentions to decide; default accept if
    # neither found (could be a fully flexible/remote posting mislabelled).
    for name in TARGET_STATE_NAMES:
        if name in desc:
            return True
    for abbrev in TARGET_STATE_ABBREVS:
        if re.search(r'\b' + abbrev.lower() + r'\b', desc):
            return True

    # Generic location with no state clue at all — accept rather than reject,
    # since these are often national/remote postings open to target-state applicants.
    return True


# ---------------------------------------------------------------------------
# Adzuna
# ---------------------------------------------------------------------------

def search_adzuna(query, location=None, page=1):
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

    url = f"{BASE_URL}/{page}"
    try:
        resp = requests.get(url, params=params, timeout=15)
        resp.raise_for_status()
        return resp.json().get("results", [])
    except Exception as e:
        print(f"  [WARN] Adzuna '{query}' in '{location or 'USA'}': {e}")
        return []


# ---------------------------------------------------------------------------
# Indeed RSS  (free, no API key)
# ---------------------------------------------------------------------------

INDEED_RSS_QUERIES = [
    "validation engineer pharma",
    "validation associate pharmaceutical",
    "equipment validation GMP",
    "process validation biotech",
    "IQ OQ PQ qualification",
]

def search_indeed_rss(query, location):
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
            # Indeed RSS entries expose title, link, published, summary (description snippet)
            # Location comes from the title pattern "Job Title - Company - City, ST"
            raw_title = entry.get("title", "")
            # Title format: "Job Title - Company Name - City, ST"
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
# BioSpace RSS  (free, pharma/biotech-specific)
# ---------------------------------------------------------------------------

BIOSPACE_QUERIES = [
    "validation engineer",
    "validation associate",
    "validation specialist",
    "equipment qualification",
    "process validation",
]

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
# Google Jobs via SerpAPI  (optional, uses free-tier credits)
# ---------------------------------------------------------------------------

def search_google_jobs(query):
    if not SERPAPI_KEY:
        return []
    params = {
        "engine": "google_jobs",
        "q": query,
        "api_key": SERPAPI_KEY,
        "chips": "date_posted:week",
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


def is_relevant(job):
    title = (job.get("title") or "").lower()

    for kw in EXCLUDE_TITLE_KEYWORDS:
        if kw in title:
            return False

    for kw in EXCLUDE_SENIORITY:
        if kw in title:
            return False

    validation_terms = ["validation", "qualification", "iq/oq", "iq oq", "gmp", "gxp", "qualify"]
    return any(t in title for t in validation_terms)


def add_jobs(candidates, seen, jobs, label=""):
    """Deduplicate and filter candidates into jobs list."""
    raw = len(candidates)
    added = 0
    for job in candidates:
        fid = job_fingerprint(job)
        if fid not in seen:
            relevant = is_relevant(job)
            in_state = is_in_target_state(job)
            if relevant and in_state:
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

    # --- Adzuna: location-specific ---
    print("Searching Adzuna (location-specific)...")
    for location in LOCATIONS:
        for query in SEARCH_QUERIES:
            add_jobs(search_adzuna(query, location=location), seen, jobs,
                     label=f"Adzuna/{location[:4]}/{query[:20]}")
            time.sleep(0.3)

    # --- Adzuna: USA-wide (catches remote + any misclassified state) ---
    print("Searching Adzuna (USA-wide)...")
    for query in SEARCH_QUERIES:
        add_jobs(search_adzuna(query), seen, jobs,
                 label=f"Adzuna/US/{query[:20]}")
        time.sleep(0.3)

    # --- Indeed RSS: location-specific (free) ---
    print("Searching Indeed RSS...")
    for location in LOCATIONS:
        for query in INDEED_RSS_QUERIES:
            add_jobs(search_indeed_rss(query, location), seen, jobs,
                     label=f"Indeed/{location[:4]}/{query[:20]}")
            time.sleep(0.5)

    # --- BioSpace RSS: pharma-specific (free) ---
    print("Searching BioSpace RSS...")
    for location in LOCATIONS:
        for query in BIOSPACE_QUERIES:
            add_jobs(search_biospace_rss(query, location), seen, jobs,
                     label=f"BioSpace/{location[:4]}/{query[:20]}")
            time.sleep(0.5)

    # --- Google Jobs via SerpAPI (optional) ---
    if SERPAPI_KEY:
        print("Searching Google Jobs (SerpAPI)...")
        for query in GOOGLE_SEARCH_QUERIES:
            raw_results = search_google_jobs(query)
            add_jobs([normalize_google_job(r) for r in raw_results], seen, jobs,
                     label=f"Google/{query[:20]}")
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
        src = job.get("_source", "Adzuna")
        source_counts[src] = source_counts.get(src, 0) + 1
    source_summary = " &bull; ".join(f"{src}: {n}" for src, n in sorted(source_counts.items()))

    html = f"""<!DOCTYPE html>
<html>
<body style="font-family:Arial,sans-serif;max-width:800px;margin:auto;padding:20px;color:#333;">
<h2 style="color:#1a73e8;">Pharma Validation Job Digest</h2>
<p style="color:#666;">{now.strftime('%B %d, %Y — %I:%M %p UTC')} &nbsp;|&nbsp; Last 3 days</p>
<p><b>Roles:</b> Validation Engineer &bull; Validation Associate &bull; Equipment Validation &bull; QA Validation &bull; Process Validation</p>
<p><b>Industry:</b> Pharma / Biopharma / Biotech / Life Sciences &nbsp;&bull;&nbsp; <b>Type:</b> Full-Time &amp; Contract &nbsp;&bull;&nbsp; <b>Level:</b> Entry–Mid</p>
<hr style="border:1px solid #eee;">
<h3 style="color:#333;">{count} job{'s' if count != 1 else ''} found</h3>
{f'<p style="color:#888;font-size:13px;">By source: {source_summary}</p>' if source_summary else ''}
"""

    if not jobs:
        html += "<p style='color:#888;'>No new matching jobs found. Check back at the next run.</p>"
    else:
        for i, job in enumerate(jobs, 1):
            title = job.get("title", "N/A")
            company = job.get("company", {}).get("display_name", "N/A")
            location = job.get("location", {}).get("display_name", "N/A")
            url = job.get("redirect_url", "#")
            created = (job.get("created") or "")[:10]
            source = job.get("_source", "Adzuna")

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
    <a href="{url}" style="color:#1a73e8;text-decoration:none;">{i}. {title}</a>
  </h3>
  <p style="margin:3px 0;font-size:14px;"><b>Company:</b> {company}</p>
  <p style="margin:3px 0;font-size:14px;"><b>Location:</b> {location}</p>
  {"<p style='margin:3px 0;font-size:14px;'><b>Salary:</b> " + salary + "</p>" if salary else ""}
  {"<p style='margin:3px 0;font-size:14px;color:#888;'>Posted: " + created + "</p>" if created else ""}
  <p style="margin:3px 0;font-size:12px;color:#aaa;">Source: {source}</p>
  <a href="{url}" style="display:inline-block;margin-top:8px;background:#1a73e8;color:white;padding:7px 16px;border-radius:4px;text-decoration:none;font-size:13px;">View &amp; Apply</a>
</div>"""

    html += "\n<hr style='border:1px solid #eee;margin-top:30px;'>"
    html += "<p style='color:#aaa;font-size:12px;'>Sources: Adzuna &bull; Indeed &bull; BioSpace &bull; Google Jobs (SerpAPI) &bull; Automated via GitHub Actions</p>"
    html += "\n</body></html>"
    return html


def send_email(jobs):
    now = datetime.now(timezone.utc)
    subject = f"[Job Digest] {len(jobs)} Pharma Validation Jobs — {now.strftime('%b %d, %I:%M %p UTC')}"

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
    print("Starting job search...")
    jobs = collect_all_jobs()
    print(f"Found {len(jobs)} relevant jobs after filtering and deduplication.")
    send_email(jobs)
    print("Done.")
