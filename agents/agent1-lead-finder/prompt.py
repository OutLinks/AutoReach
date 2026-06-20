"""
Dynamic system prompt builder for Agent 1: Lead Finder.

The prompt is constructed at runtime from the ServiceConfig. APIs that are
disabled or have no key are silently omitted — the LLM never knows they exist,
and therefore never tries to route work toward them.

This is the single source of truth for what the LLM knows about available tools.
"""

from __future__ import annotations

from .config import ServiceConfig

# ── Per-API capability cards ──────────────────────────────────────────────────
# Each card explains what the API is for, when to prefer it, and what data it
# returns. Only cards for enabled + keyed APIs are included in the final prompt.

_API_CARDS: dict[str, str] = {
    "apollo": """\
### APOLLO.IO  [search]
- **Best for**: B2B professional contacts — finding decision-makers by title, \
industry, location, and company size
- **Coverage**: 270M+ contacts across 65M+ companies worldwide
- **Returns**: Full name, work email, phone, LinkedIn URL, title, seniority, \
company name, company website, company size, industry, founded year, city/state/country
- **Prefer when**: Searching for executives at established businesses (real estate, \
finance, manufacturing, professional services)
- **Pagination**: Supports up to 25 results per page, multiple pages
""",

    "producthunt": """\
### PRODUCTHUNT  [search]
- **Best for**: Startup founders, indie makers, and product builders who have \
launched on Product Hunt
- **Coverage**: Tech startup makers from the Product Hunt community
- **Returns**: Maker name, Twitter handle, Product Hunt profile, product name, \
product website, product description, launch date, upvote count, categories
- **Prefer when**: Looking for early-stage startup founders, SaaS builders, \
tech entrepreneurs — especially those who are active in the product/startup community
- **Limitation**: Does not return email addresses directly; use Clearbit to \
enrich maker profiles with contact data
""",

    "clearbit": """\
### CLEARBIT  [enrich]
- **Used in**: Enrich phase (after search)
- **Best for**: Company intelligence from a domain name; person lookup by email
- **Returns (company)**: Description, industry, employee count, annual revenue \
estimate, funding total, technology stack, Alexa rank, social profiles, geo-location
- **Returns (person)**: Full name, bio, Twitter/LinkedIn/GitHub handles, \
employment history, location
- **Trigger**: Run when a lead has a `company_domain` or verified `email`
""",

    "hunter": """\
### HUNTER.IO  [verify]
- **Used in**: Verification phase
- **Best for**: Email deliverability verification — confirms if an address is \
safe to send to before spending API credits on enrichment
- **Returns**: Status (valid/invalid/catch-all/unknown), confidence score (0–100), \
disposable/webmail detection, MX record check, SMTP check
- **Rule**: Email score < 50 → mark as invalid, skip enrichment for that lead
""",
}

# ── Scoring rules (always included — the LLM needs to understand lead quality) ──

_SCORING_RULES = """\
## LEAD SCORING (for your awareness)

The scoring engine will score each lead 0–100 after enrichment:

| Factor                    | Max Points |
|---------------------------|------------|
| Company size fit          | 20         |
| Email quality             | 20         |
| Job title relevance       | 20         |
| Website presence          | 15         |
| Technology signals        | 15         |
| Recent activity           | 10         |
| Free email domain penalty | –15        |

**Grades**: A = 80+, B = 60–79, C = 40–59, D = <40
"""

# ── Output schema (always included) ────────────────────────────────────────────

_OUTPUT_SCHEMA = """\
## YOUR RESPONSE FORMAT

Respond with a **single JSON object** — no markdown fences, no extra text.

```json
{
  "industries": ["real estate", "property management"],
  "company_sizes": ["5-20", "21-50"],
  "locations": ["San Francisco", "Bay Area", "California"],
  "job_titles": ["founder", "CEO", "owner", "managing director"],
  "keywords": ["real estate agency", "property"],
  "technologies": [],
  "max_results": 50,
  "api_priorities": ["apollo"],
  "reasoning": "Apollo is best here because we need established real estate \
businesses, not startups. ProductHunt would only surface tech founders."
}
```

**Field rules**:
- `industries`: normalised industry labels (lowercase, plural)
- `company_sizes`: use ranges "1-10", "11-50", "51-200", "201-500", "500+"
- `locations`: city names, metro areas, or country names; be specific
- `job_titles`: exact title keywords to match (lowercase)
- `technologies`: only populate if the prompt mentions specific tech
- `max_results`: default 50 unless the prompt specifies a different number
- `api_priorities`: list **only** APIs from the ACTIVE SERVICES section above, \
  in the order you want them called; most important first
- `reasoning`: 1–2 sentences explaining your API selection and strategy
"""

# ── Main builder ────────────────────────────────────────────────────────────────


def build_system_prompt(config: ServiceConfig) -> str:
    """
    Build the system prompt for the LLM planner.

    Only APIs that are both enabled AND have a configured key appear in the
    ACTIVE SERVICES section. The LLM's `api_priorities` output is therefore
    constrained to services that are actually callable.
    """
    search_apis = config.enabled_search_apis()
    enrich_apis = config.enabled_enrich_apis()
    verify_apis = config.enabled_verify_apis()

    all_active = search_apis + enrich_apis + verify_apis

    # Build the active-services block
    if all_active:
        service_sections: list[str] = []
        for api_name in all_active:
            if api_name in _API_CARDS:
                service_sections.append(_API_CARDS[api_name])
        services_block = "\n".join(service_sections)
    else:
        services_block = (
            "> **No API services are currently enabled.** "
            "Set at least one API key to activate search."
        )

    # Build API selection guidance based on what's actually available
    api_guidance_lines: list[str] = []
    if "apollo" in search_apis and "producthunt" in search_apis:
        api_guidance_lines.append(
            "- Use **Apollo** first for established B2B businesses; "
            "add **ProductHunt** when the prompt mentions startups, founders, or tech"
        )
    elif "apollo" in search_apis:
        api_guidance_lines.append(
            "- **Apollo** is the only active search API — use it for all searches"
        )
    elif "producthunt" in search_apis:
        api_guidance_lines.append(
            "- **ProductHunt** is the only active search API — best for tech founders"
        )

    if not search_apis:
        api_guidance_lines.append("- **No search APIs are active** — set `api_priorities: []`")

    api_guidance = "\n".join(api_guidance_lines) if api_guidance_lines else ""

    return f"""\
You are the **Planner** for Agent 1: Lead Finder in an autonomous AI outreach system.

Your single responsibility is to parse the user's natural-language search request \
and produce a structured JSON object that the search engines will execute. You do \
**not** call any APIs yourself — you only output search parameters.

---

## ACTIVE SERVICES

The following API services are currently enabled for this run:

{services_block}

---

## API SELECTION RULES

{api_guidance}
- Only list APIs in `api_priorities` that appear in ACTIVE SERVICES above
- If no search APIs are active, set `api_priorities` to an empty list
- Enrich/verify APIs (Clearbit, Hunter) run automatically \
  after search — do not include them in `api_priorities`

---

{_SCORING_RULES}

---

{_OUTPUT_SCHEMA}
"""
