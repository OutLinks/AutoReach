-- AutoReach Supabase schema
--
-- Run this in the Supabase SQL editor before enabling Supabase-backed agents.
-- The app should use the service-role key from server-side code only.

create table if not exists public.leads (
    id uuid primary key,
    job_id text,

    first_name text,
    last_name text,
    full_name text,
    email text,
    phone text,

    company_name text,
    company_website text,
    company_domain text,
    company_size text,
    employee_count integer,
    industry text,
    founded_year integer,
    company_description text,
    annual_revenue text,
    funding_total text,
    funding_stage text,

    city text,
    state text,
    country text,
    timezone text,

    title text,
    seniority text,
    department text,

    linkedin_url text,
    company_linkedin_url text,
    twitter_url text,
    technologies text[] default '{}',

    email_status text,
    email_score numeric,
    website_reachable boolean,
    lead_score numeric,
    lead_grade text,
    score_breakdown jsonb default '{}'::jsonb,

    sources text[] default '{}',
    raw_data jsonb default '{}'::jsonb,
    is_duplicate boolean default false,
    stage text default 'raw',
    payload jsonb not null default '{}'::jsonb,

    created_at timestamptz default now(),
    updated_at timestamptz default now()
);

create index if not exists idx_leads_email on public.leads (email);
create index if not exists idx_leads_company_domain on public.leads (company_domain);
create index if not exists idx_leads_grade on public.leads (lead_grade);
create index if not exists idx_leads_job_id on public.leads (job_id);

create table if not exists public.research_profiles (
    id uuid primary key,
    lead_id uuid not null references public.leads(id) on delete cascade,
    job_id text,
    status text,
    quality_score jsonb default '{}'::jsonb,
    profile jsonb not null default '{}'::jsonb,
    created_at timestamptz default now(),
    updated_at timestamptz default now()
);

create index if not exists idx_research_profiles_lead_id on public.research_profiles (lead_id);
create index if not exists idx_research_profiles_job_id on public.research_profiles (job_id);

create table if not exists public.emails (
    id uuid primary key,
    lead_id uuid not null references public.leads(id) on delete cascade,
    research_profile_id uuid references public.research_profiles(id) on delete set null,

    subject text not null,
    body text not null,
    hook text,
    cta text,

    lead_first_name text,
    lead_last_name text,
    lead_company text,
    sender_name text,
    sender_email text,
    tone text,
    template_name text,

    quality_score numeric,
    quality_passed boolean default false,
    quality_report jsonb default '{}'::jsonb,

    status text default 'draft',
    job_id text,
    created_at timestamptz default now()
);

create index if not exists idx_emails_lead_id on public.emails (lead_id);
create index if not exists idx_emails_job_id on public.emails (job_id);
create index if not exists idx_emails_status on public.emails (status);

create table if not exists public.email_jobs (
    id uuid primary key,
    status text,
    total integer default 0,
    written integer default 0,
    quality_passed integer default 0,
    quality_failed integer default 0,
    skipped integer default 0,
    created_at timestamptz default now(),
    completed_at timestamptz
);

create table if not exists public.sent_emails (
    id uuid primary key,
    email_id uuid not null references public.emails(id) on delete cascade,
    lead_id uuid not null references public.leads(id) on delete cascade,
    step text,
    recipient text,
    account_email text,
    provider text,
    message_id text,
    subject text,
    body text,
    status text default 'queued',
    opened boolean default false,
    clicked boolean default false,
    replied boolean default false,
    bounced boolean default false,
    sent_at timestamptz,
    job_id text,
    created_at timestamptz default now()
);

create index if not exists idx_sent_emails_email_id on public.sent_emails (email_id);
create index if not exists idx_sent_emails_lead_id on public.sent_emails (lead_id);
create index if not exists idx_sent_emails_job_id on public.sent_emails (job_id);
create index if not exists idx_sent_emails_status on public.sent_emails (status);
create index if not exists idx_sent_emails_message_id on public.sent_emails (message_id);

create table if not exists public.tracking_events (
    id uuid primary key,
    sent_email_id uuid not null references public.sent_emails(id) on delete cascade,
    lead_id uuid references public.leads(id) on delete set null,
    event_type text not null,
    detail text,
    bounce_type text,
    occurred_at timestamptz default now(),
    metadata jsonb default '{}'::jsonb
);

create index if not exists idx_tracking_events_sent_email_id on public.tracking_events (sent_email_id);
create index if not exists idx_tracking_events_lead_id on public.tracking_events (lead_id);
create index if not exists idx_tracking_events_type on public.tracking_events (event_type);

create table if not exists public.sequence_states (
    lead_id uuid primary key references public.leads(id) on delete cascade,
    email_id uuid references public.emails(id) on delete set null,
    current_step text,
    status text,
    steps_sent jsonb default '[]'::jsonb,
    next_send_at timestamptz,
    initial_sent_at timestamptz,
    recipient text,
    account_email text,
    timezone text,
    created_at timestamptz default now(),
    updated_at timestamptz default now()
);

create index if not exists idx_sequence_states_status on public.sequence_states (status);
create index if not exists idx_sequence_states_next_send_at on public.sequence_states (next_send_at);

create table if not exists public.sending_accounts (
    email text primary key,
    provider text,
    display_name text,
    daily_limit integer,
    hourly_limit integer,
    sent_today integer default 0,
    sent_this_hour integer default 0,
    health_score numeric default 1.0,
    status text default 'active',
    warmup_start_date date
);

create table if not exists public.suppression_list (
    value text primary key,
    is_domain boolean default false,
    reason text,
    detail text,
    added_at timestamptz default now()
);

create table if not exists public.send_jobs (
    id uuid primary key,
    kind text,
    status text,
    total integer default 0,
    sent integer default 0,
    skipped integer default 0,
    failed integer default 0,
    suppressed integer default 0,
    created_at timestamptz default now(),
    completed_at timestamptz
);

create table if not exists public.conversations (
    id uuid primary key,
    lead_id uuid references public.leads(id) on delete cascade,
    recipient text,
    status text default 'active',
    message_count integer default 0,
    last_intent text,
    last_sentiment text,
    escalated boolean default false,
    created_at timestamptz default now(),
    updated_at timestamptz default now()
);

create index if not exists idx_conversations_lead_id on public.conversations (lead_id);
create index if not exists idx_conversations_status on public.conversations (status);

create table if not exists public.messages (
    id uuid primary key,
    conversation_id uuid references public.conversations(id) on delete cascade,
    lead_id uuid references public.leads(id) on delete cascade,
    direction text,
    body text,
    message_id text,
    intent text,
    sentiment text,
    action_taken text,
    created_at timestamptz default now()
);

create index if not exists idx_messages_conversation_id on public.messages (conversation_id);
create index if not exists idx_messages_lead_id on public.messages (lead_id);
create index if not exists idx_messages_message_id on public.messages (message_id);

create table if not exists public.handoffs (
    id text primary key,
    lead_id uuid references public.leads(id) on delete cascade,
    reason text,
    urgency text,
    summary text,
    suggested_response text,
    conversation_excerpt text,
    created_at timestamptz default now()
);

create index if not exists idx_handoffs_lead_id on public.handoffs (lead_id);

create table if not exists public.notifications (
    id uuid primary key,
    kind text,
    lead_id uuid references public.leads(id) on delete cascade,
    title text,
    message text,
    urgency text,
    created_at timestamptz default now()
);

create index if not exists idx_notifications_lead_id on public.notifications (lead_id);
create index if not exists idx_notifications_created_at on public.notifications (created_at);

create table if not exists public.reply_jobs (
    id uuid primary key,
    status text,
    total integer default 0,
    handled integer default 0,
    escalated integer default 0,
    replies_sent integer default 0,
    meetings_booked integer default 0,
    skipped integer default 0,
    created_at timestamptz default now(),
    completed_at timestamptz
);

alter table public.leads enable row level security;
alter table public.research_profiles enable row level security;
alter table public.emails enable row level security;
alter table public.email_jobs enable row level security;
alter table public.sent_emails enable row level security;
alter table public.tracking_events enable row level security;
alter table public.sequence_states enable row level security;
alter table public.sending_accounts enable row level security;
alter table public.suppression_list enable row level security;
alter table public.send_jobs enable row level security;
alter table public.conversations enable row level security;
alter table public.messages enable row level security;
alter table public.handoffs enable row level security;
alter table public.notifications enable row level security;
alter table public.reply_jobs enable row level security;
