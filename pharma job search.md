# Pharma Job Search Agents

Two automated job search agents running via GitHub Actions, emailing daily/twice-daily digests of pharma validation and quality roles.

---

## Agent 1 — General Pharma Validation (MA / NH / RI / CT)

**Script:** `job_search_agent/job_search.py`
**Workflow:** `.github/workflows/job_search.yml`
**Schedule:** 8:00 AM + 2:00 PM EDT, Mon–Fri

### What It Searches
- **States:** Massachusetts, New Hampshire, Rhode Island, Connecticut
- **Company:** Any pharma / biopharma / biotech / life sciences employer
- **Roles:** Validation Engineer, Validation Associate, Validation Analyst, Validation Specialist, Equipment Validation, Equipment Qualification, Process Validation, GMP Validation, IQ OQ PQ, Qualification Engineer, Qualification Specialist, Quality Assurance Validation

### Sources
| Source | Cost | Notes |
|--------|------|-------|
| Adzuna | Free | Location-specific + USA-wide searches |
| Indeed RSS | Free | 5 queries × 4 states |
| BioSpace RSS | Free | Pharma/biotech niche board |
| Google Jobs (SerpAPI) | 100 credits/month free | Skipped if no `SERPAPI_KEY` |

### GitHub Secrets Required
| Secret | Purpose |
|--------|---------|
| `ADZUNA_APP_ID` | Adzuna API app ID |
| `ADZUNA_API_KEY` | Adzuna API key |
| `GMAIL_USER` | Gmail account for sending |
| `GMAIL_APP_PASSWORD` | Gmail app password |
| `RECIPIENT_EMAIL` | Comma-separated recipient emails |
| `SERPAPI_KEY` | SerpAPI key for Google Jobs (optional) |

---

## Agent 2 — AbbVie Only (Massachusetts)

**Script:** `job_search_abbvie/job_search.py`
**Workflow:** `.github/workflows/job_search_abbvie.yml`
**Schedule:** 9:00 AM EDT, Mon–Fri (once daily)

### What It Searches
- **State:** Massachusetts only
- **Company:** AbbVie only (`abbvie`, `abbvie inc`)
- **Roles:** All validation roles below + quality roles

#### Validation Roles
Validation Engineer, Validation Associate, Validation Specialist, Equipment Validation, Process Validation, GMP Validation, Qualification Engineer, IQ OQ PQ

#### Quality Roles
Quality Engineer, Quality Assurance, Quality Associate, Quality Specialist, Quality Control, QA, QC, QA/QC

### Sources
| Source | Cost | Notes |
|--------|------|-------|
| AbbVie Careers (Workday direct) | Free | Hits `abbvie.wd3.myworkdayjobs.com` API directly |
| Adzuna | Free | MA-specific + USA-wide |
| Indeed RSS | Free | MA-specific |
| BioSpace RSS | Free | MA-specific |
| Google Jobs (SerpAPI) | 100 credits/month free | AbbVie + MA query |

### `is_relevant()` Filter — Title Keywords

**Accepts titles containing any of:**

| Term | Match type | Example titles matched |
|------|-----------|----------------------|
| `validation` | phrase | Validation Engineer, Process Validation Specialist |
| `qualification` | phrase | Qualification Engineer, IQ OQ PQ |
| `iq/oq`, `iq oq` | phrase | IQ OQ PQ Specialist |
| `gmp`, `gxp` | phrase | GMP Compliance Associate |
| `quality engineer` | phrase | Quality Engineer I |
| `quality assurance` | phrase | Quality Assurance Associate |
| `quality associate` | phrase | Quality Associate |
| `quality specialist` | phrase | Quality Specialist |
| `quality control` | phrase | Quality Control Analyst |
| `qa/qc` | phrase | QA/QC Specialist |
| `\bqa\b` | regex (word boundary) | QA Analyst, Validation QA, Associate QA |
| `\bqc\b` | regex (word boundary) | QC Inspector, Validation QC |

> **Note:** `qa` and `qc` use regex word boundaries (`\bqa\b`, `\bqc\b`) instead of simple substring match to avoid false positives like "aqua" or "squad".

**Excludes titles containing:**
- `computer system validation`, `csv validation`, `it validation`, `software validation`, `computer systems validation`
- `senior`, `sr.`, `lead`, `manager`, `director`, `principal`, `head of`, `vp`, `vice president`, `staff engineer`

### `is_in_target_state()` Filter — Location Logic

Rule: **never scan job descriptions to reject.** Only use the structured location field.

1. Remote / empty location → **accept**
2. Target state found in location → **accept**
3. Non-target state found in location → **reject**
4. Generic location (city-only, "united states") → scan description for target state mentions; default **accept**

> **Why this rule exists:** Pharma job descriptions almost always mention NJ, PA, MD (big pharma HQ states, FDA in Maryland). Scanning descriptions to reject caused every legitimate MA job to be falsely filtered out.

### GitHub Secrets Required
Same as Agent 1 — no additional secrets needed.

---

## Bug History — `is_in_target_state()` (General Agent)

Three rounds of filter bugs all caused zero results:

**Bug 1 (original):** `"united states" in loc → return True` — allowed ALL US jobs through.

**Bug 2 (Apr 21, commit `00ed2ed`):** Fix added description abbreviation scanning. `\bor\b` (Oregon), `\bin\b` (Indiana), `\bme\b` (Maine) are common English words — every description was rejected.

**Bug 3 (Apr 29, commit `41a11af`):** Moved abbreviation checks to location field only but still scanned descriptions for full state names like "new jersey", "maryland". Pharma descriptions almost always mention NJ/PA/MD → every MA/CT job rejected.

**Final fix (Apr 29, commit `9ce2e48`) ✅ WORKING:** Descriptions are never used as a basis for rejection. Only structured location field is checked.

---

## Debugging Tips

- Each source logs `raw=X kept=Y` in GitHub Actions output.
- If `raw=0` across all sources → Adzuna API key issue or no jobs posted that day.
- AbbVie Workday direct returns `raw=0` → Workday API endpoint may have changed; check `abbvie.wd3.myworkdayjobs.com`.
