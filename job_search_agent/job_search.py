import os
import time
import hashlib
import requests
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

# Broad queries used for both USA-wide and location-specific searches
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

# Location-specific searches (NE states + major pharma hubs)
LOCATIONS = [
    "Massachusetts",
    "New Hampshire",
    "Rhode Island",
    "Connecticut",
    "New Jersey",
    "Pennsylvania",
    "North Carolina",
    "California",
]

# Single broad query for Google Jobs — keeps usage within SerpAPI free tier (100/month)
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


def search_jobs(query, location=None, page=1):
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

    url = f"{BASE_URL}/{page}"
    try:
        resp = requests.get(url, params=params, timeout=15)
        resp.raise_for_status()
        return resp.json().get("results", [])
    except Exception as e:
        print(f"  [WARN] '{query}' in '{location or 'USA'}': {e}")
        return []


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
    }


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


def collect_all_jobs():
    seen = set()
    jobs = []

    # Location-specific Adzuna searches (better precision for target states)
    for location in LOCATIONS:
        for query in SEARCH_QUERIES:
            for job in search_jobs(query, location=location):
                fid = job_fingerprint(job)
                if fid not in seen and is_relevant(job):
                    seen.add(fid)
                    jobs.append(job)
            time.sleep(0.3)

    # USA-wide Adzuna search (catches remote + any remaining states)
    for query in SEARCH_QUERIES:
        for job in search_jobs(query):
            fid = job_fingerprint(job)
            if fid not in seen and is_relevant(job):
                seen.add(fid)
                jobs.append(job)
        time.sleep(0.3)

    # Google Jobs search via SerpAPI (skipped if SERPAPI_KEY not set)
    if SERPAPI_KEY:
        print("Running Google Jobs search...")
        for query in GOOGLE_SEARCH_QUERIES:
            for raw in search_google_jobs(query):
                job = normalize_google_job(raw)
                fid = job_fingerprint(job)
                if fid not in seen and is_relevant(job):
                    seen.add(fid)
                    jobs.append(job)
            time.sleep(0.5)
    else:
        print("SERPAPI_KEY not set — skipping Google Jobs search.")

    return jobs


def build_html(jobs):
    now = datetime.now(timezone.utc)
    count = len(jobs)

    html = f"""<!DOCTYPE html>
<html>
<body style="font-family:Arial,sans-serif;max-width:800px;margin:auto;padding:20px;color:#333;">
<h2 style="color:#1a73e8;">Pharma Validation Job Digest</h2>
<p style="color:#666;">{now.strftime('%B %d, %Y — %I:%M %p UTC')} &nbsp;|&nbsp; Last 7 days</p>
<p><b>Roles:</b> Validation Engineer &bull; Validation Associate &bull; Equipment Validation &bull; QA Validation &bull; Process Validation</p>
<p><b>Industry:</b> Pharma / Biopharma / Biotech / Life Sciences &nbsp;&bull;&nbsp; <b>Type:</b> Full-Time &amp; Contract &nbsp;&bull;&nbsp; <b>Level:</b> Entry–Mid</p>
<hr style="border:1px solid #eee;">
<h3 style="color:#333;">{count} job{'s' if count != 1 else ''} found</h3>
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
    html += "<p style='color:#aaa;font-size:12px;'>Sources: Adzuna &bull; Google Jobs (SerpAPI) &bull; Automated via GitHub Actions</p>"
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
