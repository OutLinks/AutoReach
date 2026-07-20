"""
Conversation loader (Input Layer #3).

Enriches a bare reply with the context the understanding + action layers need:
  - the original email being replied to (from Agent 3's emails.db),
  - lead identity + lead score (from Agent 1's leads output, for escalation),
  - how many prior exchanges this thread already has (from the conversation store).

Everything is best-effort: missing sources degrade gracefully rather than block
reply handling.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path
from typing import Generator, Optional

from core.runtime_paths import agent_output_dir

from ...models import IncomingReply
from ...storage.conversation_store import ConversationStore

logger = logging.getLogger(__name__)

_AGENT1_OUTPUT_DIR = agent_output_dir("agent1-lead-finder")


class ConversationLoader:
    def __init__(
        self,
        emails_db_path: str,
        store: ConversationStore,
        leads_dir: Optional[Path] = None,
    ) -> None:
        self._emails_db = emails_db_path
        self._store = store
        self._leads_dir = leads_dir or _AGENT1_OUTPUT_DIR
        self._lead_index: dict[str, dict] = {}

    def enrich(self, reply: IncomingReply) -> IncomingReply:
        email = self._original_email(reply.email_id)
        if email:
            reply.original_subject = email.get("subject", "")
            reply.original_body = email.get("body", "")
            reply.lead_first_name = email.get("lead_first_name", "")
            reply.lead_company = email.get("lead_company", "")

        lead = self._lead_for(reply.lead_id)
        if lead:
            reply.from_email = reply.from_email or (lead.get("email") or "")
            reply.lead_first_name = reply.lead_first_name or (lead.get("first_name") or "")
            reply.lead_company = reply.lead_company or (lead.get("company_name") or "")
            # Agent 1 scores 0–100; the doc's escalation threshold is on a 0–10
            # scale ("lead score > 8"), so normalize here.
            reply.lead_score = float(lead.get("lead_score") or 0.0) / 10.0

        reply.prior_exchanges = self._store.count_exchanges(reply.lead_id)
        return reply

    # ── Sources ────────────────────────────────────────────────────────────────

    def _original_email(self, email_id: str) -> Optional[dict]:
        if not email_id or not Path(self._emails_db).exists():
            return None
        try:
            conn = sqlite3.connect(f"file:{self._emails_db}?mode=ro", uri=True)
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM emails WHERE id = ?", (email_id,)
            ).fetchone()
            conn.close()
        except sqlite3.Error as exc:
            logger.warning("ConversationLoader: emails.db read failed — %s", exc)
            return None
        return dict(row) if row else None

    def _lead_for(self, lead_id: str) -> Optional[dict]:
        if not lead_id:
            return None
        if not self._lead_index:
            self._build_lead_index()
        return self._lead_index.get(lead_id)

    def _build_lead_index(self) -> None:
        if not self._leads_dir.exists():
            return
        for path in sorted(self._leads_dir.glob("leads_*.jsonl")):
            for lead in self._stream(path):
                lid = lead.get("id")
                if lid:
                    self._lead_index[lid] = lead

    @staticmethod
    def _stream(path: Path) -> Generator[dict, None, None]:
        try:
            with open(path) as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            yield json.loads(line)
                        except json.JSONDecodeError:
                            continue
        except OSError:
            return
