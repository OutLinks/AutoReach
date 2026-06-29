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
    "google_places": """\
### GOOGLE PLACES  [search]
- **Best for**: Local and regional business discovery by category and location
- **Returns**: Business name, website, phone, address, ratings, categories, hours
- **Prefer when**: Searching agencies, realtors, dentists, lawyers, restaurants, \
local services, or companies with physical offices
- **Limitation**: Does not return employee emails; Hunter Domain Search runs later
""",

    "tavily": """\
### TAVILY  [search]
- **Best for**: AI-oriented public web search and company context discovery
- **Returns**: Relevant pages, snippets/summaries, company websites, recent context
- **Prefer when**: Searching software companies, niche markets, competitors, pain \
points, or non-local business categories
- **Limitation**: Returns web evidence, not guaranteed contact records
""",

    "hunter_domain_search": """\
### HUNTER DOMAIN SEARCH  [enrich]
- **Used in**: Enrich phase (after search)
- **Best for**: Finding work emails from a company domain
- **Returns**: Email, confidence, source, pattern, first/last name, role metadata
- **Trigger**: Run when a lead has a `company_domain`
""",

    "wappalyzer": """\
### WAPPALYZER  [enrich]
- **Used in**: Enrich phase (after search)
- **Best for**: Detecting website technologies for personalization
- **Returns**: CMS, frameworks, analytics, hosting/CDN, payment tools, marketing tools
- **Trigger**: Run when a lead has a `company_website`
""",

    "crunchbase": """\
### CRUNCHBASE  [enrich]
- **Used in**: Enrich phase (after search)
- **Best for**: Startup/company intelligence
- **Returns**: Funding, investors, founders, company age, employee estimates, categories
- **Trigger**: Run when a lead has a company name or domain
""",

    "whoisxml": """\
### WHOISXML  [enrich]
- **Used in**: Enrich phase (after search)
- **Best for**: Domain registration intelligence
- **Returns**: Domain age, registrar, creation date, expiration, name servers
- **Trigger**: Run when a lead has a `company_domain`
""",

    "securitytrails": """\
### SECURITYTRAILS  [enrich]
- **Used in**: Enrich phase (after search)
- **Best for**: DNS intelligence and technical enrichment
- **Returns**: Subdomains, DNS records/history, hosting and infrastructure signals
- **Trigger**: Run when a lead has a `company_domain`
""",

    "abstract": """\
### ABSTRACT EMAIL VALIDATION  [verify]
- **Used in**: Verification phase
- **Best for**: Email deliverability verification — confirms if an address is \
safe to send to
- **Returns**: Deliverability, quality score, syntax, disposable status, MX, SMTP, catch-all
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
  "api_priorities": ["google_places"],
  "reasoning": "Google Places is best here because we need established local real \
estate businesses in specific locations. Tavily can supplement web context."
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
    if "google_places" in search_apis and "tavily" in search_apis:
        api_guidance_lines.append(
            "- Use **Google Places** first for local/business-category searches; "
            "use **Tavily** for web-first, startup, SaaS, competitor, or pain-point searches"
        )
    elif "google_places" in search_apis:
        api_guidance_lines.append(
            "- **Google Places** is the only active search API — use it for business discovery"
        )
    elif "tavily" in search_apis:
        api_guidance_lines.append(
            "- **Tavily** is the only active search API — use it for web-first discovery"
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
- Enrich/verify APIs run automatically \
  after search — do not include them in `api_priorities`

---

{_SCORING_RULES}

---

{_OUTPUT_SCHEMA}
"""
