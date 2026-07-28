# 🤖 Multi-Agent AI Outreach System — Orchestrator & Agent Deep Dive

> Complete architecture and explanation for building an autonomous AI outreach system with 5 specialized agents and a central orchestrator.

---

## 📂 Files in This Document

| Section | Description |
|---------|-------------|
| **Orchestrator** | The central brain that manages all agents |
| **Agent 1: Lead Finder** | Finds and qualifies prospects |
| **Agent 2: Research Analyst** | Deeply researches each lead |
| **Agent 3: Email Writer** | Writes personalized emails |
| **Agent 4: Sender + Follow-Up** | Sends emails and manages sequences |
| **Agent 5: Reply Handler** | Reads replies and takes action |

---

---

# 🎼 The Orchestrator

The Orchestrator is the **brain of your entire system**. It doesn't do the actual work — the agents do that. Instead, it manages, coordinates, monitors, and optimizes the entire pipeline.

## Core Responsibilities

```
1. TRIGGER — Decides WHEN each agent runs
2. CONTROL — Decides WHAT each agent works on
3. MONITOR — Watches for errors and bottlenecks
4. DECIDE — Handles branching logic and edge cases
5. OPTIMIZE — Learns from results and improves
6. REPORT — Tells you what's happening
```

Think of it as the CEO of your AI outreach operation.

---

## The Lead Lifecycle (State Machine)

Every lead moves through a pipeline of states. The Orchestrator manages these states and decides when to move leads between them.

### State Flow

```
NEW → DISCOVERED → RESEARCHING → RESEARCHED → WRITING → READY → SENT
                                                                    │
                                              ┌─────────────────────┤
                                              │                     │
                                              ▼                     ▼
                                           REPLIED            NO REPLY
                                              │                     │
                                        Agent 5 runs          Agent 4 runs
                                              │                follow-up
                                              ▼                     │
                                        HANDLED                     ▼
                                              │                CLOSED
                                              ▼
                                        MEETING BOOKED
```

### Orchestrator's Role at Each State

**When lead enters "DISCOVERED":**
- Is Agent 2 idle or busy?
- If idle: Immediately trigger Agent 2
- If busy: Queue the lead
- Set timeout: If Agent 2 doesn't complete in 30 minutes, flag for review

**When lead enters "RESEARCHED":**
- Does research meet quality standards?
- If yes: Trigger Agent 3
- If no: Re-queue for Agent 2 or flag for human
- Is this lead high-priority? Move to front of queue

**When lead enters "READY":**
- What's our sending capacity today?
- If under limit: Schedule for Agent 4
- If at limit: Queue for tomorrow
- What's the best send time for this lead's timezone?

**When lead enters "SENT":**
- Start timer: If no reply in 3 days, trigger follow-up
- Monitor: Did the email bounce? Mark as invalid if yes
- Update: Decrease today's sending capacity

**When lead enters "REPLIED":**
- Immediately trigger Agent 5
- Pause all follow-up sequences
- If Agent 5 confident: Let it handle automatically
- If Agent 5 uncertain: Queue for human review

---

## Daily Schedule

```
6:00 AM — Health Check
  ├─ Check all agents are running
  ├─ Check API limits
  ├─ Check sender reputation scores
  ├─ Review yesterday's results
  └─ Send daily summary to human

7:00 AM — Agent 1 (Lead Finder)
  ├─ Search for new leads
  ├─ Verify and enrich
  └─ Add to database

8:00 AM — Agent 2 (Research Analyst)
  ├─ Pick up all "DISCOVERED" leads
  ├─ Research each lead deeply
  └─ Update status to "RESEARCHED"

9:00 AM — Agent 3 (Email Writer)
  ├─ Pick up all "RESEARCHED" leads
  ├─ Write personalized emails
  └─ Update status to "READY"

9:30 AM — Agent 4 (Sender) — Initial emails
  ├─ Check sending capacity
  ├─ Calculate optimal send times
  ├─ Send initial emails
  └─ Update status to "SENT"

10:00 AM — Agent 5 (Reply Handler)
  ├─ Check inbox for new replies
  ├─ Classify each reply
  ├─ Generate responses
  └─ Send responses

12:00 PM — Midday Check
  ├─ Emails sent this morning?
  ├─ Any bounces or complaints?
  ├─ Replies needing human attention?
  └─ Adjust afternoon schedule

3:00 PM — Agent 5 — Afternoon reply check

6:00 PM — Agent 4 (Follow-Up Manager)
  ├─ Day 3 follow-ups
  ├─ Day 7 follow-ups
  └─ Breakup emails (Day 14)

8:00 PM — Evening Report
  ├─ Compile today's metrics
  ├─ Compare to historical averages
  ├─ Flag anomalies
  └─ Send summary to human
```

### Timezone Intelligence

The Orchestrator adapts to each lead's timezone:

```
Lead in New York (EST): Send at 10:00 AM EST
Lead in Los Angeles (PST): Send at 10:00 AM PST
Lead in London (GMT): Send at 10:00 AM GMT
Lead in Tokyo (JST): Send at 10:00 AM JST
```

Leads are batched by timezone and scheduled accordingly.

---

## Queue Management

The Orchestrator manages waiting lines for each agent.

### Priority Queue

```
Priority Factors:
- Lead quality score (from Agent 1)
- Company size (bigger = higher priority)
- Industry fit (better fit = higher priority)
- Recency (newer leads = higher priority)
- Manual flag (human can bump a lead)

Priority Score = (Quality × 0.4) + (Size × 0.2) + (Fit × 0.2) + (Recency × 0.2)
```

### Concurrency Limits

```
Agent 1: Unlimited (batch operation)
Agent 2: 5 concurrent leads (API-intensive)
Agent 3: 10 concurrent leads (faster)
Agent 4: Limited by sending capacity
Agent 5: 3 concurrent replies (needs care)
```

### Queue Health Monitoring

```
RED FLAGS:
- Queue growing faster than processing
- Lead stuck in queue > 24 hours
- Agent processing time increasing
- Error rate > 5%

ACTIONS:
- Alert human if queue exceeds threshold
- Auto-scale by reducing batch size
- Re-prioritize if timeouts detected
- Skip leads stuck too long
```

---

## Error Handling

### Error Types

**API Errors:** Rate limits, auth failures, timeouts, bad responses, service down

**Data Errors:** Missing fields, invalid formats, malformed JSON, encoding issues, duplicates

**Agent Errors:** Crashes, low quality output, nonsensical output, infinite loops

**System Errors:** Database connection lost, disk full, memory exceeded, network down

### Error Response Strategy

**Transient Errors (retry might work):**
```
1. Log the error
2. Wait 30 seconds → Retry
3. Wait 2 minutes → Retry
4. Wait 10 minutes → Retry
5. If still failing → Mark as failed, alert human
```

**Permanent Errors (retry won't help):**
```
1. Log the error
2. Mark lead as "error" with details
3. Move to next lead
4. Alert human with summary
5. Human decides: retry, skip, or fix
```

**Cascading Errors:**
```
1. Detect root cause
2. Pause all affected agents
3. Alert human immediately
4. Wait for human intervention
5. Resume when resolved
6. Reprocess affected leads
```

### Circuit Breaker Pattern

```
CLOSED STATE: Normal operation, count failures
OPEN STATE: Too many failures, stop trying, wait cooldown
HALF-OPEN: Allow one test request, if success → CLOSED, if fail → OPEN
```

### Dead Letter Queue

Leads that repeatedly fail go to a holding area for weekly human review.

---

## Configuration Management

### Targeting Criteria

```
TARGET:
  Industries: ["real estate", "property management"]
  Company sizes: ["5-20", "21-50"]
  Locations: ["San Francisco", "Bay Area", "California"]
  Job titles: ["founder", "ceo", "owner", "managing director"]
  Technologies: ["wordpress", "mailchimp", "hubspot"]
  Founded: "2018-2025"
```

### Volume Settings

```
VOLUME:
  Leads per day: 50
  Emails per day: 50
  Follow-ups per day: 100
  Max concurrent research: 5
  Max concurrent writing: 10
```

### Quality Thresholds

```
QUALITY:
  Minimum email score: 70
  Minimum research completeness: 80%
  Minimum personalization score: 7
  Maximum spam risk: low
  Minimum lead quality for outreach: 6/10
```

### Escalation Rules

```
ESCALATION:
  Low confidence classification: < 70%
  High-value lead: score > 8
  Angry lead: negative sentiment
  Custom pricing request: always
  Technical question: beyond basic info
  Multiple replies: > 3 exchanges
  Bounce rate: > 5%
  Complaint rate: > 0.1%
```

### Sender Reputation Limits

```
REPUTATION:
  Daily send limit per account: 50
  Hourly send limit per account: 10
  Minimum time between emails to same domain: 30 seconds
  Warm-up period for new accounts: 14 days
  Bounce rate threshold for pause: 5%
  Complaint rate threshold for pause: 0.1%
```

---

---

# 🔍 Agent 1: Lead Finder

Finds qualified prospects and adds them to the database.

## What It Does

```
INPUT:  "Find 50 real estate agencies in San Francisco 
          with 5-50 employees"

OUTPUT: 50 verified leads in Airtable:
  - Name, email, company, website, LinkedIn
  - Email verified (deliverable)
  - Duplicates checked
  - Lead quality score assigned
  - Status: "new"
```

## Data Sources

### Tavily Search API (Public Web Search)
- Find companies, context, competitors, pain points, and recent public pages
- Returns: search results, AI summaries, relevant pages, and snippets
- Handles natural-language lead searches when a Tavily key is configured

### Public Web Scraper
- Starts from company or directory URLs supplied in a query or Runtime Settings
- Follows direct public company links and extracts public company/contact details
- Does not use structured lead databases or bypass access controls

## Enrichment

| Field | Source | Why |
|-------|--------|-----|
| Website | Tavily / public web scraper | For research |
| Email | Hunter Domain Search | Contact discovery |
| Email verification | Abstract Email Validation | Deliverability |
| Technologies | Wappalyzer | Pain point inference |
| Recent news | GNews | Email personalization |
| Startup intelligence | Crunchbase | Funding and growth context |
| Domain age | WhoisXML | Company maturity signal |
| DNS intelligence | SecurityTrails | Technical enrichment |
| Repositories | GitHub API | Product and engineering signals |

## Email Verification

Using Abstract Email Validation:
- Check deliverability (valid/invalid)
- Score (0-100)
- Detect disposable emails
- Check syntax, MX, SMTP, and catch-all status
- Reject if score < 50

Hunter is used for Domain Search contact discovery, not final verification.

## Lead Scoring

```
Scoring Factors (0-100):
- Company size fit (0-20)
- Email quality (0-20)
- Job title relevance (0-20)
- Website quality (0-15)
- Technology signals (0-15)
- Recent activity (0-10)
- Penalty for free email domains (-15)

Grades: A (80+), B (60-79), C (40-59), D (<40)
```

## Deduplication

Check before adding:
- Email exact match
- Company + name match
- LinkedIn URL match
- Website + title match

## Cost

| Tool | Free | Paid |
|------|------|------|
| Tavily | Free tier | Low monthly/API usage |
| Hunter Domain Search | Free tier | Paid tiers |
| Abstract Email Validation | Free tier | Low monthly/API usage |
| Wappalyzer | Limited/free options | Paid tiers |
| GNews | Free tier | Paid tiers |
| Crunchbase | API plan | Paid tiers |
| Firecrawl | Free tier | Paid tiers |

**Minimum: $0/month with free tiers/self-hosted fallbacks | Recommended: low recurring API spend**

---

---

# 🔬 Agent 2: Research Analyst

Deeply researches each lead to find personalization angles.

## What It Does

```
INPUT:  Lead data (name, company, website, LinkedIn, title)

OUTPUT: Deep research profile:
  - Company summary
  - Pain points (with evidence)
  - Personal notes
  - Email angle
  - Recent events
  - Competitor insights
  - Social media presence
  - Tech stack analysis
  - Lead quality score
  - Recommended approach
```

## Data Collection

### Website Scraping

Pages to scrape:
| Page | What to Look For |
|------|------------------|
| Homepage | Value proposition, target customer, services |
| About | Company story, founder background, mission |
| Team | Key people, roles, backgrounds |
| Services/Pricing | What they offer, pricing model |
| Blog | Recent topics, content frequency |
| Case Studies | Who they work with, results |
| Contact | Phone, email, locations |
| Careers | Open positions, growth signals |
| Testimonials | Client feedback, common themes |
| Footer | Social links, company age |

### Public Web Search

Search with Tavily:
- "{company} news 2025 2026"
- "{company} competitors"
- "{company} reviews"
- "{name} interview"
- "{industry} trends 2026"
- "{industry} challenges 2026"
- "{company} hiring"

### Company News

Search with GNews:
- Funding announcements
- Product launches
- Partnerships
- Expansion and hiring news

### Technology and Developer Signals

Collect with Wappalyzer and GitHub API:
- CMS, frameworks, analytics, hosting/CDN
- Payment, marketing, and support tooling
- Public repositories, languages, releases, stars, and activity

### Legacy Search Patterns

Search queries:
- "{company} competitors"
- "{company} reviews"
- "{name} interview"
- "{company} hiring"

## AI Analysis

The AI analyzes all collected data to produce:

**Company Profile:**
- What they do (2-3 sentences)
- Who their customers are
- Business model
- Growth stage
- Competitive advantage

**Pain Point Analysis:**
- 3-5 specific pain points
- Evidence for each
- Severity rating
- Relevance to your service

**Personal Profile:**
- Background and expertise
- Likely priorities
- Communication style
- What would resonate
- Personal connections

**Email Angle:**
- Best hook for opening
- Which pain point to reference
- Relevant social proof
- Recommended CTA

**Lead Quality Score (1-10):**
- Decision-making power
- Company fit
- Pain likelihood
- Reachability
- Timing

## Cost

**Cost per lead: ~$0.10-0.50**
**100 leads: ~$10-50**

---

---

# ✍️ Agent 3: Email Writer

Takes research and crafts personalized emails.

## What It Does

```
INPUT:  Research from Agent 2

OUTPUT: Personalized email:
  - Subject line (2 variants for A/B testing)
  - Email body (under 150 words)
  - Personalization score (1-10)
  - Spam risk assessment
  - Follow-up sequence (3-4 emails)
  - Status: "ready_to_send"
```

## Template Library

| Template | When to Use |
|----------|-------------|
| Problem-Agitation | Lead has clear pain point |
| Curiosity | Lead is busy/important |
| Social Proof | Similar company got results |
| Question | Lead is thoughtful |
| Direct | Lead is busy/no-nonsense |
| Reference | Lead posted something recent |
| Competitor | Competitor is doing something |
| Event-Based | Lead had a recent event |

## Subject Line Formulas

| Formula | Example |
|---------|---------|
| Question | "Quick question about Bay Area Realty?" |
| Company + Value | "Bay Area Realty + AI automation" |
| Number | "3 ways to cut lead response time by 90%" |
| How | "How [similar company] doubled conversions" |
| Curiosity | "Saw this and thought of you" |
| Direct | "15-min chat about {{company}}?" |
| Reference | "Loved your post on AI in real estate" |

## Email Structure

```
[HOOK] - 1-2 sentences (specific reference)
    ↓
[PROBLEM] - 1-2 sentences (their pain)
    ↓
[SOLUTION] - 1-2 sentences (your approach)
    ↓
[SOCIAL PROOF] - 1 sentence (concrete result)
    ↓
[CTA] - 1 sentence (low-pressure question)
```

## Quality Checks

1. **Personalization Score** (must be >= 7/10)
   - Uses first name
   - References company name
   - References specific pain point
   - References recent event
   - Includes specific result

2. **Spam Risk** (must be low)
   - No spam trigger words
   - No excessive caps
   - No excessive exclamation marks
   - Subject line under 10 words
   - Not too many links

3. **Tone Match**
   - Formal for executives
   - Casual for founders
   - Technical for CTOs

4. **Length Check**
   - 50-150 words ideal
   - Under 50 = too short
   - Over 200 = too long

## Follow-Up Sequence

```
Day 0: Initial email (personalized, problem-focused)
Day 3: Follow-up #1 (add value — share resource)
Day 7: Follow-up #2 (different angle — social proof)
Day 14: Breakup email (respectful, last chance)
```

## Cost

**Cost per lead: ~$0.08**
**100 leads: ~$8**

---

---

# 📤 Agent 4: Sender + Follow-Up Manager

Sends emails at the right time and manages follow-up sequences.

## What It Does

```
INPUT:  Emails from Agent 3 + lead data

OUTPUT:
  - Emails sent at optimal times
  - Follow-up sequence managed
  - Delivery status tracked
  - Sending reputation protected
  - Agent 5 notified of replies
```

## Scheduling

### Optimal Send Times

| Day | Best Time | Why |
|-----|-----------|-----|
| Monday | Avoid | Too busy catching up |
| Tuesday | 9:00-11:00 AM | Fresh start |
| Wednesday | 9:00-11:00 AM | Mid-week, productive |
| Thursday | 9:00-11:00 AM | Still productive |
| Friday | Avoid | Winding down |
| Weekend | Avoid | Not professional |

### Timezone Handling

Each lead gets emailed at 10:00 AM in their local timezone.

### Volume Limits

```
New accounts (first 2 weeks): 20/day
Warming up (weeks 3-4): 30/day
Established (4+ weeks): 50/day
Premium (good reputation): 100/day

Hourly limit: 10
Burst limit: 3 per minute
```

### Account Rotation

Multiple sending accounts distribute volume:
- Round-robin with health weighting
- Sort by health score (highest first)
- Then by sent count (lowest first)

## Sending Platforms

### Instantly.ai (Recommended)
- Built for cold email
- Handles deliverability and warm-up
- Built-in tracking
- API for automation

### Gmail API (Alternative)
- Send directly via Gmail
- Requires SPF/DKIM/DMARC setup
- Lower volume limits

### AWS SES (Programmable Delivery)
- Send through Amazon Simple Email Service
- Requires verified sender/domain and AWS credentials
- Configure with `AGENT4_PROVIDER=ses` and `AWS_SES_REGION`

### Custom SMTP (Maximum Control)
- Your own mail server
- Full control over sending
- Requires reputation management

## Tracking

| Event | How | Why |
|-------|-----|-----|
| Sent | API confirmation | Confirm delivery |
| Delivered | SMTP response | Confirm inbox placement |
| Opened | Tracking pixel | Measure subject line |
| Clicked | Link tracking | Measure content |
| Replied | Email monitoring | Trigger Agent 5 |
| Bounced | SMTP response | Clean list |
| Unsubscribed | Link click | Compliance |
| Spam complaint | Feedback loop | Protect reputation |

## Sequence Management

```
State Machine:
READY → SENT → (wait 3 days) → FOLLOW_UP_1 → (wait 4 days) → FOLLOW_UP_2 → (wait 7 days) → BREAKUP → CLOSED

At any point:
SENT → REPLIED → Agent 5 handles
SENT → MEETING_BOOKED → Stop sequence, notify human
```

## Reputation Management

### Bounce Handler
- Hard bounce → Mark invalid, remove from list
- Soft bounce → Retry up to 3 times, then treat as hard
- Block bounce → Check for reputation issue

### Complaint Handler
- Immediately mark as complained
- Add to suppression list (never email again)
- Pause sending if complaint rate > 0.1%

### Sender Score Monitoring
```
Warning thresholds:
- Bounce rate > 2%
- Complaint rate > 0.1%
- Open rate < 15%

Critical thresholds:
- Bounce rate > 5%
- Complaint rate > 0.3%

Action: Pause sending immediately if critical
```

## A/B Testing

Test these elements:
- Subject lines (A vs B)
- Opening lines
- CTA style
- Send times
- Email length
- Template type

Minimum 100 samples per variant before declaring winner.

## Cost

**~$42-62/month** (Instantly.ai + tracking server + database)

---

---

# 🧠 Agent 5: Reply Handler

Reads replies, understands intent, and takes action.

## What It Does

```
INPUT:  Email replies from leads

OUTPUT:
  - Intent classification
  - Auto-response (if appropriate)
  - Meeting booking (if interested)
  - Lead status update
  - Human notification (if needed)
  - Conversation memory
```

## Intent Categories

| Intent | Description | Action |
|--------|-------------|--------|
| INTERESTED | Wants to learn more | Send Calendly link |
| NOT_INTERESTED | Says no | Send polite goodbye |
| QUESTION | Has a specific question | Answer question |
| OBJECTION | Has concerns | Handle objection |
| WRONG_PERSON | Not right contact | Ask for referral |
| OUT_OF_OFFICE | Auto-reply | Pause sequence |
| ALREADY_CUSTOMER | Already using service | Route to support |
| NEEDS_TIME | Interested but not now | Schedule follow-up |
| CONFUSED | Doesn't understand | Clarify value prop |
| ANGRY | Upset about email | Apologize + remove |
| MEETING_BOOKED | Already booked | Confirm + notify human |
| UNCLEAR | Can't determine | Ask clarifying question |

## Reply Detection

### Method 1: Instantly.ai Webhook
Instantly sends a webhook when a reply is received.

### Method 2: Gmail API Polling
Poll inbox for replies every 2 hours.

### Method 3: IMAP IDLE
Real-time reply detection without polling.

## Auto-Reply Generation

### For Interested Leads
```
"Hi {{name}},

Thanks for getting back! I'd love to show you how this works.

Here's my calendar:
{{calendly_link}}

Looking forward to chatting!

Best,
{{your_name}}"
```

### For Questions
```
"Hi {{name}},

Great question!

{{answer}}

Want to hop on a quick call to discuss?
{{calendly_link}}

Best,
{{your_name}}"
```

### For Objections
```
"Hi {{name}},

Totally understand. {{acknowledgment}}

{{reframe_with_proof}}

Worth a quick chat?
{{calendly_link}}

Best,
{{your_name}}"
```

### For Not Interested
```
"Hi {{name}},

No problem at all — I appreciate you letting me know.

If anything changes, feel free to reach out.

Best of luck with {{company}}!

{{your_name}}"
```

## Objection Handling

| Objection | Strategy |
|-----------|----------|
| Already have a solution | Differentiate, don't badmouth competitor |
| Too expensive | Show ROI, offer pilot |
| No time to implement | Offer done-for-you |
| Need to think about it | Create urgency, share case study |
| Not the right time | Schedule future follow-up |
| We're too small | Show similar-size results |
| Tried something similar | Acknowledge, differentiate |
| Need to talk to team | Offer to present to team |

## Meeting Booking

When a lead is interested:
1. Send Calendly link with pre-filled data
2. When booked, update Airtable to "meeting_booked"
3. Send confirmation email with:
   - Meeting time and link
   - Preparation questions
   - What to expect
4. Notify human immediately
5. Stop all follow-up sequences

## Human Handoff

Escalate when:
- Classification confidence < 70%
- Lead score > 8 (high-value)
- Negative sentiment (angry/upset)
- Custom pricing request
- Technical question beyond basic info
- More than 3 reply exchanges
- Competitor mentioned by name
- Pricing negotiation

## Conversation Memory

Store every message:
- Incoming and outgoing
- Timestamps
- Message IDs
- Metadata (intent, sentiment, action taken)

Generate summaries for human handoff.

## Cost

**Cost per reply: ~$0.05**
**100 replies: ~$5**

---

---

# 💰 Total System Cost

| Component | Monthly Cost |
|-----------|-------------|
| Agent 1 (Lead Finder) | $0-150 |
| Agent 2 (Research) | $10-50 |
| Agent 3 (Writing) | $5-10 |
| Agent 4 (Sending) | $42-62 |
| Agent 5 (Replies) | $5-15 |
| Orchestrator (n8n) | $0-20 |
| **Total** | **~$62-307/month** |

---

# 🔑 Key Principles

1. **Quality over quantity** — 50 personalized emails beats 500 generic ones
2. **Protect sender reputation** — One spam complaint can destroy deliverability
3. **Always have human escape hatch** — AI should know when to ask for help
4. **Track everything** — Every interaction should be logged
5. **Learn from every interaction** — Use data to improve
6. **Ship fast, iterate later** — Get the basic flow working first
7. **Recurring revenue focus** — Prioritize retainers and subscriptions
8. **Build in public** — Share your journey, attract an audience

---

*Research compiled: June 2026*
