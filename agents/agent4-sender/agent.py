"""
Agent 4: Sender + Follow-Up Manager

Reads approved emails from Agent 3, sends them at the right time through the
right account, tracks engagement, runs the 4-touch follow-up sequence, and
protects sender reputation — pausing follow-ups when a lead replies and,
when enabled, notifying Agent 5.

Five-layer pipeline:
  Layer 1 — Scheduling  : timezone → send-time → volume → warm-up → ScheduledSend
  Layer 2 — Sending     : pluggable email APIs or SMTP (account rotation)
  Layer 3 — Tracking    : delivery, open, click, reply
  Layer 4 — Sequence    : day0 → day3 → day7 → day14 breakup (state machine)
  Layer 5 — Reputation  : bounce, complaint, spam-trap, sender-score (pause gate)

Two batch entry points (run by the orchestrator) plus event hooks the tracking
server / provider webhooks call:

    agent = SenderAgent(ServiceConfig.from_env())
    await agent.run_initial()     # send day-0 emails for all approved leads
    await agent.run_followups()   # send any follow-ups that are now due

    agent.handle_reply(sent_id, "looks interesting")   # → pauses follow-ups
    agent.handle_bounce(sent_id, "hard")
    agent.handle_complaint(sent_id)
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from datetime import datetime, timezone
from typing import Optional
from uuid import uuid4
from uuid import NAMESPACE_URL, uuid5

from .accounts import load_accounts
from .config import ServiceConfig
from .models import STEP_DAY0, SendJob, SentEmail
from .storage.email_reader import EmailReader
from .storage.send_store import SendStore
from .storage.d1_send_store import D1SendStore
from .layers.scheduling.scheduler import SchedulingLayer
from .layers.sending.sender import SendingLayer
from .layers.tracking.tracker import TrackingLayer
from .layers.tracking.reply_detector import ReplyNotification
from .layers.sequence.sequence_manager import SequenceLayer
from .layers.reputation.reputation_manager import ReputationLayer

logger = logging.getLogger(__name__)


class SenderAgent:
    def __init__(self, config: ServiceConfig) -> None:
        self._config = config

        self._reader = EmailReader(config.emails_db_path, backend=config.email_reader_backend)
        self._store = D1SendStore() if config.storage_backend == "d1" else SendStore(config.db_path)

        accounts = load_accounts(config)
        self._scheduling = SchedulingLayer(config, accounts)
        for account in self._scheduling.accounts:
            self._store.upsert_account(account)

        self._sending = SendingLayer(config)
        self._tracking = TrackingLayer(config, self._store)
        self._sequence = SequenceLayer(config, self._store)
        self._reputation = ReputationLayer(config, self._store)

    def get_sent_record(self, sent_id: str) -> Optional[dict]:
        return self._store.get_sent(sent_id)

    def list_sent_records(self) -> list[dict]:
        return self._store.list_sent()

    def events_for(self, sent_id: str) -> list[dict]:
        return self._store.get_events(sent_id)

    def latest_provider_message_id(self, lead_id: str) -> str:
        sent = self._store.latest_sent_for_lead(lead_id) or {}
        return str(sent.get("message_id") or "")

    def sequence_for(self, lead_id: str):
        return self._store.get_sequence(lead_id)

    # ── Batch: initial (day-0) sends ───────────────────────────────────────────

    async def run_initial(
        self,
        job_id: Optional[str] = None,
        lead_ids: Optional[list[str]] = None,
    ) -> SendJob:
        """Send the day-0 email for every approved lead with capacity."""
        job_id = job_id or str(uuid4())
        logger.info("SenderAgent: starting initial-send job %s", job_id)

        emails = self._reader.read_sendable(lead_ids=lead_ids)
        job = SendJob(id=job_id, kind="initial", total=len(emails), status="in_progress")
        self._store.upsert_job(job)

        if not emails:
            return self._finalize(job)

        sem = asyncio.Semaphore(self._config.concurrency)

        async def process(email: dict) -> None:
            async with sem:
                await self._send_initial(email, job)

        await asyncio.gather(*(process(e) for e in emails))

        # Health gate: pause everything if reputation went critical mid-batch.
        status = self._reputation.enforce(self._scheduling.accounts)
        if status.should_pause:
            job.status = "paused"

        return self._finalize(job)

    async def _send_initial(self, email: dict, job: SendJob) -> None:
        recipient = email.get("recipient", "")
        if not recipient:
            if self._config.simulate:
                correlation_id = email.get("lead_id") or email.get("id") or "unknown"
                recipient = f"simulated@{correlation_id}.invalid"
                logger.info(
                    "SenderAgent[sim]: using a non-routable recipient for email %s",
                    email.get("id"),
                )
            else:
                logger.warning(
                    "SenderAgent: no recipient for email %s — skipping", email.get("id")
                )
                job.skipped += 1
                return

        allowed, reason = self._reputation.can_email(recipient)
        if not allowed:
            logger.info("SenderAgent: suppressing %s (%s)", recipient, reason)
            job.suppressed += 1
            return

        scheduled = self._scheduling.schedule(
            {**email, "recipient": recipient},
            step=STEP_DAY0,
        )
        if scheduled is None:
            job.skipped += 1  # no account capacity → queue for next run
            return

        account = self._scheduling.account_for(scheduled.account_email)
        sent = await self._dispatch(
            email_id=email.get("id", ""),
            lead_id=email.get("lead_id", ""),
            step=STEP_DAY0,
            recipient=recipient,
            account_email=scheduled.account_email,
            subject=email.get("subject", ""),
            body=email.get("body", ""),
            from_name=email.get("sender_name", ""),
            job_id=job.id,
        )
        if sent is None:
            job.failed += 1
            return

        job.sent += 1
        if self._config.followups_enabled:
            # Kick off the follow-up sequence only when the campaign permits it.
            self._sequence.start(
                lead_id=sent.lead_id,
                email_id=sent.email_id,
                sent_at=sent.sent_at or datetime.now(timezone.utc),
                recipient=recipient,
                account_email=scheduled.account_email,
                timezone_name=scheduled.timezone,
            )

    # ── Batch: follow-ups ──────────────────────────────────────────────────────

    async def run_followups(
        self,
        job_id: Optional[str] = None,
        now: Optional[datetime] = None,
    ) -> SendJob:
        """Send every follow-up that is currently due across active sequences."""
        job_id = job_id or str(uuid4())
        now = now or datetime.now(timezone.utc)
        logger.info("SenderAgent: starting follow-up job %s", job_id)

        if not self._config.followups_enabled:
            return self._finalize(
                SendJob(id=job_id, kind="followups", total=0, status="in_progress")
            )

        due = self._sequence.due(now)
        job = SendJob(id=job_id, kind="followups", total=len(due), status="in_progress")
        self._store.upsert_job(job)

        if not due:
            return self._finalize(job)

        sem = asyncio.Semaphore(self._config.concurrency)

        async def process(state) -> None:
            async with sem:
                await self._send_followup(state, job, now)

        await asyncio.gather(*(process(s) for s in due))

        self._reputation.enforce(self._scheduling.accounts)
        return self._finalize(job)

    async def run_scheduled_followup(
        self,
        lead_id: str,
        expected_step: str,
        job_id: str,
        now: Optional[datetime] = None,
    ) -> SendJob:
        """Execute one Workflow-addressed sequence step, safely and idempotently."""
        now = now or datetime.now(timezone.utc)
        state = self._store.get_sequence(lead_id)
        job = SendJob(id=job_id, kind="followups", status="in_progress")
        if (
            state is None
            or not state.is_active
            or state.current_step != expected_step
            or state.next_send_at is None
        ):
            return self._finalize(job)
        due = state.next_send_at
        if due.tzinfo is None:
            due = due.replace(tzinfo=timezone.utc)
        if due > now:
            raise RuntimeError("Follow-up Workflow fired before its persisted UTC deadline")
        job.total = 1
        self._store.upsert_job(job)
        await self._send_followup(state, job, now)
        return self._finalize(job)

    async def send_message(
        self,
        *,
        email_id: str,
        lead_id: str,
        recipient: str,
        subject: str,
        body: str,
        from_name: str = "",
        job_id: str | None = None,
        step: str = STEP_DAY0,
        in_reply_to: str = "",
        references: str = "",
    ) -> Optional[SentEmail]:
        """Send one API-approved message through the normal safety and tracking path."""
        if not recipient:
            raise ValueError("Recipient email is required")
        allowed, reason = self._reputation.can_email(recipient)
        if not allowed:
            raise ValueError(f"Recipient is suppressed: {reason}")
        scheduled = self._scheduling.schedule(
            {
                "id": email_id,
                "lead_id": lead_id,
                "recipient": recipient,
            },
            step=step,
            # A human-authored reply is part of an existing conversation, not
            # another cold-send attempt to the same domain.
            ignore_domain_spacing=step == "manual_reply",
        )
        if scheduled is None:
            raise ValueError("No sending account currently has capacity")
        return await self._dispatch(
            email_id=email_id,
            lead_id=lead_id,
            step=step,
            recipient=recipient,
            account_email=scheduled.account_email,
            subject=subject,
            body=body,
            from_name=from_name,
            job_id=job_id or str(uuid4()),
            in_reply_to=in_reply_to,
            references=references,
        )

    async def _send_followup(self, state, job: SendJob, now: datetime) -> None:
        allowed, reason = self._reputation.can_email(state.recipient)
        if not allowed:
            logger.info("SenderAgent: follow-up suppressed for %s (%s)", state.recipient, reason)
            self._sequence.stop(state.lead_id)
            job.suppressed += 1
            return

        original = self._reader.read_by_id(state.email_id) or {}
        draft = await self._sequence.writer.write(
            state.current_step,
            first_name=original.get("lead_first_name", "there"),
            company=original.get("lead_company", "your company"),
            original_subject=original.get("subject", ""),
            original_body=original.get("body", ""),
            sender_name=original.get("sender_name", ""),
            value_proposition=original.get("tone", ""),
        )

        # Thread the follow-up onto the previous message.
        prev = self._store.latest_sent_for_lead(state.lead_id) or {}
        prev_mid = prev.get("message_id", "")

        sent = await self._dispatch(
            email_id=state.email_id,
            lead_id=state.lead_id,
            step=state.current_step,
            recipient=state.recipient,
            account_email=state.account_email,
            subject=draft.subject,
            body=draft.body,
            from_name=original.get("sender_name", ""),
            job_id=job.id,
            in_reply_to=prev_mid,
            references=prev_mid,
        )
        if sent is None:
            job.failed += 1
            return

        job.sent += 1
        self._sequence.advance(state, sent.sent_at or now)

    # ── Shared dispatch path ───────────────────────────────────────────────────

    async def _dispatch(
        self,
        *,
        email_id: str,
        lead_id: str,
        step: str,
        recipient: str,
        account_email: str,
        subject: str,
        body: str,
        from_name: str,
        job_id: str,
        in_reply_to: str = "",
        references: str = "",
    ) -> Optional[SentEmail]:
        account = self._scheduling.account_for(account_email)
        if account is None:
            logger.error("SenderAgent: account %s not found", account_email)
            return None

        # Reserve the delivery before contacting a provider. Queue and
        # Workflow retries use the same key; an ambiguous previous attempt is
        # never resent automatically, preventing duplicate outreach.
        idempotency_key = hashlib.sha256(
            f"{email_id}\x1f{lead_id}\x1f{step}".encode("utf-8")
        ).hexdigest()
        existing = self._store.get_sent_by_idempotency_key(idempotency_key)
        if existing:
            if existing.get("status") in {"sent", "delivered", "opened", "clicked", "replied"}:
                return SentEmail.model_validate(existing)
            logger.warning("SenderAgent: delivery %s is awaiting reconciliation", idempotency_key)
            return None

        reserve = getattr(self._store, "reserve_capacity", None)
        if callable(reserve) and not reserve(
            idempotency_key=idempotency_key,
            account_email=account_email,
            recipient=recipient,
            reserved_at=datetime.now(timezone.utc),
            daily_limit=account.daily_limit,
            hourly_limit=account.hourly_limit,
            burst_per_minute=self._config.burst_per_minute,
            domain_spacing_seconds=(
                0 if step == "manual_reply" else self._config.min_seconds_between_same_domain
            ),
        ):
            logger.info("SenderAgent: durable volume limit denied %s", recipient)
            return None

        # Build the record first so tracking can key off its id.
        sent = SentEmail(
            id=str(uuid5(NAMESPACE_URL, f"autoreach:{idempotency_key}")),
            email_id=email_id,
            lead_id=lead_id,
            step=step,
            recipient=recipient,
            account_email=account_email,
            provider=account.provider,
            subject=subject,
            body=body,
            status="queued",
            job_id=job_id,
            idempotency_key=idempotency_key,
        )

        sent.status = "sending"
        self._store.insert_sent(sent)

        instrumented_body = self._tracking.instrument(body, sent.id)
        result = await self._sending.deliver(
            account=account,
            to_email=recipient,
            subject=subject,
            body=instrumented_body,
            from_name=from_name,
            in_reply_to=in_reply_to,
            references=references,
        )

        if not result.success:
            sent.status = "failed"
            self._store.insert_sent(sent)
            return None

        sent.message_id = result.message_id
        sent.status = "sent"
        sent.sent_at = datetime.now(timezone.utc)
        self._store.insert_sent(sent)

        # Confirm delivery (simulation/SMTP-accept) → "delivered".
        self._tracking.on_send_accepted(sent.id, lead_id)
        return sent

    # ── Event hooks (called by webhooks / tracking server) ─────────────────────

    def handle_reply(
        self, sent_email_id: str, snippet: str = "", provider_event_id: str = ""
    ) -> Optional[ReplyNotification]:
        """A reply arrived → pause the sequence and optionally hand off to Agent 5."""
        notification = self._tracking.record_reply(
            sent_email_id, snippet, provider_event_id
        )
        if notification:
            self._sequence.pause_on_reply(notification.lead_id)
        return notification

    def handle_bounce(self, sent_email_id: str, bounce_type: str = "hard", detail: str = "") -> str:
        disposition = self._reputation.bounce.handle(sent_email_id, bounce_type, detail)
        if disposition == "suppressed":
            sent = self._store.get_sent(sent_email_id)
            if sent:
                self._sequence.mark_bounced(sent.get("lead_id", ""))
        return disposition

    def handle_complaint(self, sent_email_id: str, detail: str = "") -> bool:
        recorded = self._reputation.complaint.handle(sent_email_id, detail)
        if recorded:
            sent = self._store.get_sent(sent_email_id)
            if sent:
                self._sequence.stop(sent.get("lead_id", ""))
        return recorded

    def record_open(self, sent_email_id: str, user_agent: str = "") -> None:
        self._tracking.open.record_open(sent_email_id, user_agent=user_agent)

    def record_click(self, sent_email_id: str, url: str) -> None:
        self._tracking.click.record_click(sent_email_id, url)

    def reputation_status(self, account_email: str = "*"):
        return self._reputation.health(account_email)

    def close(self) -> None:
        """Release the sender's SQLite connection."""
        self._store.close()

    # ── Internal ───────────────────────────────────────────────────────────────

    def _finalize(self, job: SendJob) -> SendJob:
        if job.status != "paused":
            job.status = "complete"
        job.completed_at = datetime.now(timezone.utc)
        self._store.upsert_job(job)
        logger.info(
            "SenderAgent: job %s done — sent=%d skipped=%d suppressed=%d failed=%d (%s)",
            job.id, job.sent, job.skipped, job.suppressed, job.failed, job.status,
        )
        return job
