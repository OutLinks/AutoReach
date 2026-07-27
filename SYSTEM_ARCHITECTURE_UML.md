# AutoReach System Architecture — UML Diagrams

These diagrams describe the implemented single-instance MVP. The API is a
control plane; all long-running work is persisted as jobs and executed by one
worker because the orchestration and agent integrations share local SQLite and
file-based artifacts.

## 1. Deployment and component diagram

```mermaid
classDiagram
    direction TB

    class APIClient {
      <<external>>
      HTTP client / operator
    }
    class FastAPI {
      <<component>>
      api.main:create_app
      campaign, pipeline, event routes
    }
    class ConfigStore {
      <<component>>
      runtime configuration
    }
    class HourlyScheduler {
      <<component>>
      submits scheduled ticks
    }
    class JobExecutor {
      <<component>>
      single async worker
      executes durable jobs
    }
    class JobStore {
      <<component>>
      SQLite api_jobs
    }
    class Orchestrator {
      <<component>>
      lead lifecycle owner
    }
    class OrchestratorStore {
      <<component>>
      SQLite master records
    }
    class Agent1 {
      <<agent>>
      Lead Finder
    }
    class Agent2 {
      <<agent>>
      Research Analyst
    }
    class Agent3 {
      <<agent>>
      Email Writer
    }
    class Agent4 {
      <<agent>>
      Sender / Follow-up
    }
    class Agent5 {
      <<agent>>
      Reply Handler
    }
    class ProviderServices {
      <<external>>
      LLM, search, email providers
    }
    class LocalArtifacts {
      <<database>>
      shared SQLite + JSON files
    }

    APIClient --> FastAPI : HTTPS
    FastAPI --> ConfigStore
    FastAPI --> JobExecutor : submit jobs
    FastAPI --> JobStore : inspect jobs
    HourlyScheduler --> JobExecutor : submit ticks
    JobExecutor --> JobStore : claim / update
    JobExecutor --> Orchestrator : invoke pipeline
    Orchestrator --> OrchestratorStore : leads, events, runs, campaigns
    Orchestrator --> Agent1 : find
    Orchestrator --> Agent2 : research
    Orchestrator --> Agent3 : write
    Orchestrator --> Agent4 : send / follow-up
    Orchestrator --> Agent5 : reply
    Agent1 --> LocalArtifacts
    Agent2 --> LocalArtifacts
    Agent3 --> LocalArtifacts
    Agent4 --> LocalArtifacts
    Agent5 --> LocalArtifacts
    Agent1 --> ProviderServices
    Agent2 --> ProviderServices
    Agent3 --> ProviderServices
    Agent4 --> ProviderServices
    Agent5 --> ProviderServices
```

## 2. Runtime class diagram

```mermaid
classDiagram
    direction LR

    class AppSettings {
      +data_dir: Path
      +scheduler_interval_seconds: float
      +from_env() AppSettings
      +validate()
    }
    class FastAPIApp {
      +state.orchestrator: Orchestrator
      +state.job_store: JobStore
      +state.executor: JobExecutor
      +state.scheduler: HourlyScheduler
    }
    class JobStore {
      +create(kind, payload, dedupe_key) JobRecord
      +recover_incomplete() list~str~
      +mark_running(job_id)
      +mark_succeeded(job_id, result)
      +mark_failed(job_id, error)
    }
    class JobRecord {
      +id: str
      +kind: str
      +status: queued|running|succeeded|failed
      +payload: dict
      +result: Any
    }
    class JobExecutor {
      -queue: asyncio.Queue~str~
      -worker: asyncio.Task
      +start()
      +submit(kind, payload, dedupe_key) JobRecord
      -_execute(job) Any
    }
    class HourlyScheduler {
      +start()
      +stop()
    }
    class Orchestrator {
      +create_campaign(prompt) CampaignBrief
      +run_find() StageResult
      +run_stage(stage) StageResult
      +run_cycle() dict
      +tick(now) dict
      +health() HealthSnapshot
      +report() DailyReport
    }
    class OrchestratorStore {
      +upsert_lead(lead)
      +get_lead(id) PipelineLead
      +record_run(run)
      +log_event(lead_id, from, to, note)
    }
    class Stage {
      +name: str
      +agent: str
      +from_state: str
      +success_state: str
    }
    class PipelineLead {
      +id: str
      +state: str
      +attempts: int
      +retry_after: datetime
    }

    AppSettings --> FastAPIApp : configures
    FastAPIApp *-- JobStore
    FastAPIApp *-- JobExecutor
    FastAPIApp *-- HourlyScheduler
    FastAPIApp *-- Orchestrator
    JobStore "1" o-- "*" JobRecord : persists
    JobExecutor --> JobStore
    JobExecutor --> Orchestrator
    HourlyScheduler --> JobExecutor
    Orchestrator *-- OrchestratorStore
    Orchestrator --> Stage : executes
    OrchestratorStore "1" o-- "*" PipelineLead : persists
```

## 3. Pipeline job sequence diagram

```mermaid
sequenceDiagram
    autonumber
    actor Operator
    participant API as FastAPI API
    participant Jobs as JobStore (SQLite)
    participant Worker as JobExecutor
    participant Orchestrator
    participant Store as OrchestratorStore
    participant Agent as Stage adapter / agent

    Operator->>API: POST pipeline or campaign endpoint
    API->>Jobs: create queued JobRecord
    API->>Worker: submit(job id)
    Worker->>Jobs: mark_running(job id)
    Worker->>Orchestrator: run_find(), run_stage(), run_cycle(), or tick()
    Orchestrator->>Store: read campaign and eligible leads
    Orchestrator->>Agent: ingest() and execute(stage context)
    Agent-->>Orchestrator: StageResult + outcomes
    Orchestrator->>Store: persist lead transitions and run history
    Orchestrator-->>Worker: result
    Worker->>Jobs: mark_succeeded(job id, result)
    API-->>Operator: 202 / job reference; GET job for outcome

    Note over Worker,Jobs: On restart, queued and running jobs are requeued.
    Note over Worker,Agent: One worker protects shared local agent artifacts.
```

## 4. Lead lifecycle state diagram

```mermaid
stateDiagram-v2
    [*] --> DISCOVERED : Agent 1 / find
    DISCOVERED --> RESEARCHING : Agent 2 starts
    RESEARCHING --> RESEARCHED : research succeeds
    RESEARCHED --> WRITING : Agent 3 starts
    WRITING --> READY : email passes quality gate
    READY --> SENDING : Agent 4 starts
    SENDING --> SENT : delivery accepted
    SENT --> FOLLOWING_UP : scheduled follow-up
    FOLLOWING_UP --> FOLLOWING_UP : next sequence step
    SENT --> REPLIED : inbound reply
    FOLLOWING_UP --> REPLIED : inbound reply
    REPLIED --> HANDLING : Agent 5 starts
    HANDLING --> HANDLED : reply resolved
    HANDLING --> MEETING_BOOKED : meeting arranged
    HANDLED --> MEETING_BOOKED : meeting arranged
    SENT --> MEETING_BOOKED : direct booking
    SENT --> NO_REPLY : sequence exhausted
    FOLLOWING_UP --> NO_REPLY : sequence exhausted
    NO_REPLY --> CLOSED

    DISCOVERED --> CLOSED : incomplete / rejected
    RESEARCHED --> CLOSED : low-quality research
    READY --> CLOSED : send blocked
    SENT --> CLOSED : bounce / stop
    REPLIED --> CLOSED : decline / stop
    HANDLING --> CLOSED : decline / stop
    HANDLED --> CLOSED : completed without meeting

    DISCOVERED --> ERROR
    RESEARCHING --> ERROR
    RESEARCHED --> ERROR
    WRITING --> ERROR
    READY --> ERROR
    SENDING --> ERROR
    SENT --> ERROR
    FOLLOWING_UP --> ERROR
    REPLIED --> ERROR
    HANDLING --> ERROR
    ERROR --> DISCOVERED : retry
    ERROR --> RESEARCHED : retry
    ERROR --> READY : retry
    ERROR --> SENT : retry
    ERROR --> REPLIED : retry
    ERROR --> DEAD : retries exhausted

    MEETING_BOOKED --> [*]
    CLOSED --> [*]
    DEAD --> [*]
```

## Source map

- API lifecycle and dependency wiring: `api/main.py`
- Durable single-consumer execution: `api/executor.py`, `api/jobs.py`, and `api/scheduler.py`
- Master pipeline coordination: `orchestrator/orchestrator.py`
- State and stage contracts: `orchestrator/state_machine.py`
- Live-agent integration boundary: `orchestrator/adapters/live.py`
