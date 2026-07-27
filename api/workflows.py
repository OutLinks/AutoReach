"""Durable, user-editable API workflow records backed by SQLite."""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, default=str)


class WorkflowStore:
    """Persistence used by the interactive research, writing, and inbox APIs."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._conn:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS research_documents (
                    id         TEXT PRIMARY KEY,
                    lead_id    TEXT,
                    company    TEXT NOT NULL,
                    prompt     TEXT NOT NULL DEFAULT '',
                    summary    TEXT NOT NULL DEFAULT '',
                    sections   TEXT NOT NULL DEFAULT '[]',
                    sources    TEXT NOT NULL DEFAULT '[]',
                    version    INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_research_lead_updated
                    ON research_documents (lead_id, updated_at DESC);

                CREATE TABLE IF NOT EXISTS email_drafts (
                    id              TEXT PRIMARY KEY,
                    lead_id         TEXT NOT NULL,
                    campaign_id     TEXT NOT NULL,
                    subject         TEXT NOT NULL DEFAULT '',
                    body            TEXT NOT NULL DEFAULT '',
                    status          TEXT NOT NULL DEFAULT 'draft',
                    version         INTEGER NOT NULL DEFAULT 1,
                    tone            TEXT NOT NULL DEFAULT '',
                    instructions    TEXT NOT NULL DEFAULT '',
                    source_email_id TEXT NOT NULL DEFAULT '',
                    created_at      TEXT NOT NULL,
                    updated_at      TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_drafts_lookup
                    ON email_drafts (lead_id, campaign_id, status, updated_at DESC);

                CREATE TABLE IF NOT EXISTS lead_searches (
                    id          TEXT PRIMARY KEY,
                    query       TEXT NOT NULL,
                    filters     TEXT NOT NULL DEFAULT '{}',
                    result_limit INTEGER NOT NULL,
                    status      TEXT NOT NULL DEFAULT 'queued',
                    found_count INTEGER NOT NULL DEFAULT 0,
                    error       TEXT NOT NULL DEFAULT '',
                    created_at  TEXT NOT NULL,
                    updated_at  TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_searches_created
                    ON lead_searches (created_at DESC);

                CREATE TABLE IF NOT EXISTS lead_search_results (
                    search_id TEXT NOT NULL,
                    lead_id   TEXT NOT NULL,
                    payload   TEXT NOT NULL,
                    imported  INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (search_id, lead_id)
                );
                CREATE INDEX IF NOT EXISTS idx_search_results_search
                    ON lead_search_results (search_id);

                CREATE TABLE IF NOT EXISTS mailbox_threads (
                    id         TEXT PRIMARY KEY,
                    subject    TEXT NOT NULL DEFAULT '',
                    lead_id    TEXT NOT NULL,
                    last_from  TEXT NOT NULL DEFAULT '',
                    unread     INTEGER NOT NULL DEFAULT 0,
                    bounced    INTEGER NOT NULL DEFAULT 0,
                    replied    INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_mailbox_updated
                    ON mailbox_threads (updated_at DESC);

                CREATE TABLE IF NOT EXISTS mailbox_messages (
                    id         TEXT PRIMARY KEY,
                    thread_id  TEXT NOT NULL,
                    direction  TEXT NOT NULL,
                    from_addr  TEXT NOT NULL DEFAULT '',
                    to_addr    TEXT NOT NULL DEFAULT '',
                    subject    TEXT NOT NULL DEFAULT '',
                    body       TEXT NOT NULL DEFAULT '',
                    sent_at    TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_mailbox_messages_thread
                    ON mailbox_messages (thread_id, sent_at);

                CREATE TABLE IF NOT EXISTS operator_conversations (
                    id         TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS operator_messages (
                    id              TEXT PRIMARY KEY,
                    conversation_id TEXT NOT NULL,
                    role            TEXT NOT NULL,
                    content         TEXT NOT NULL,
                    tool_calls      TEXT,
                    created_at      TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_operator_messages_conversation
                    ON operator_messages (conversation_id, created_at);

                CREATE TABLE IF NOT EXISTS operator_timeline (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    conversation_id TEXT NOT NULL,
                    step            TEXT NOT NULL,
                    agent           TEXT NOT NULL,
                    action          TEXT NOT NULL,
                    status          TEXT NOT NULL,
                    started_at      TEXT,
                    finished_at     TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_operator_timeline_conversation
                    ON operator_timeline (conversation_id, id);
                """
            )

    # Research

    def create_research(
        self,
        *,
        lead_id: str = "",
        company: str,
        prompt: str = "",
        summary: str = "",
        sections: list[dict[str, str]] | None = None,
        sources: list[dict[str, str]] | None = None,
    ) -> dict[str, Any]:
        document_id = str(uuid4())
        now = _now()
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO research_documents (
                    id, lead_id, company, prompt, summary, sections, sources,
                    version, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                """,
                (
                    document_id,
                    lead_id or None,
                    company,
                    prompt,
                    summary,
                    _json(sections or []),
                    _json(sources or []),
                    now,
                    now,
                ),
            )
        return self.get_research(document_id)

    def list_research(self, lead_id: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            if lead_id:
                rows = self._conn.execute(
                    """
                    SELECT * FROM research_documents
                    WHERE lead_id = ? ORDER BY updated_at DESC
                    """,
                    (lead_id,),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM research_documents ORDER BY updated_at DESC"
                ).fetchall()
        return [self._research_row(row) for row in rows]

    def get_research(self, document_id: str) -> dict[str, Any]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM research_documents WHERE id = ?", (document_id,)
            ).fetchone()
        if row is None:
            raise KeyError(document_id)
        return self._research_row(row)

    def update_research(self, document_id: str, values: dict[str, Any]) -> dict[str, Any]:
        allowed = {"lead_id", "company", "summary", "sections", "sources"}
        updates = {key: value for key, value in values.items() if key in allowed}
        if not updates:
            return self.get_research(document_id)
        if "sections" in updates:
            updates["sections"] = _json(updates["sections"])
        if "sources" in updates:
            updates["sources"] = _json(updates["sources"])
        updates["updated_at"] = _now()
        columns = ", ".join(f"{key} = ?" for key in updates)
        with self._lock, self._conn:
            cursor = self._conn.execute(
                f"""
                UPDATE research_documents
                SET {columns}, version = version + 1
                WHERE id = ?
                """,
                (*updates.values(), document_id),
            )
        if cursor.rowcount != 1:
            raise KeyError(document_id)
        return self.get_research(document_id)

    @staticmethod
    def _research_row(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "lead_id": row["lead_id"],
            "company": row["company"],
            "summary": row["summary"],
            "sections": json.loads(row["sections"] or "[]"),
            "sources": json.loads(row["sources"] or "[]"),
            "version": row["version"],
            "updated_at": row["updated_at"],
        }

    # Email drafts

    def create_draft(
        self,
        *,
        lead_id: str,
        campaign_id: str,
        subject: str,
        body: str,
        tone: str = "",
        instructions: str = "",
        source_email_id: str = "",
    ) -> dict[str, Any]:
        draft_id = str(uuid4())
        now = _now()
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO email_drafts (
                    id, lead_id, campaign_id, subject, body, status, version,
                    tone, instructions, source_email_id, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 'draft', 1, ?, ?, ?, ?, ?)
                """,
                (
                    draft_id,
                    lead_id,
                    campaign_id,
                    subject,
                    body,
                    tone,
                    instructions,
                    source_email_id,
                    now,
                    now,
                ),
            )
        return self.get_draft(draft_id)

    def list_drafts(
        self,
        *,
        lead_id: str | None = None,
        campaign_id: str | None = None,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        for column, value in (
            ("lead_id", lead_id),
            ("campaign_id", campaign_id),
            ("status", status),
        ):
            if value:
                clauses.append(f"{column} = ?")
                params.append(value)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._lock:
            rows = self._conn.execute(
                f"SELECT * FROM email_drafts {where} ORDER BY updated_at DESC",
                params,
            ).fetchall()
        return [self._draft_row(row) for row in rows]

    def get_draft(self, draft_id: str) -> dict[str, Any]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM email_drafts WHERE id = ?", (draft_id,)
            ).fetchone()
        if row is None:
            raise KeyError(draft_id)
        return self._draft_row(row)

    def get_draft_internal(self, draft_id: str) -> dict[str, Any]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM email_drafts WHERE id = ?", (draft_id,)
            ).fetchone()
        if row is None:
            raise KeyError(draft_id)
        payload = dict(row)
        return payload

    def update_draft(self, draft_id: str, values: dict[str, Any]) -> dict[str, Any]:
        updates = {
            key: value for key, value in values.items() if key in {"subject", "body"}
        }
        if not updates:
            return self.get_draft(draft_id)
        draft = self.get_draft_internal(draft_id)
        if draft["status"] == "sent":
            raise ValueError("Sent emails cannot be edited")
        updates["status"] = "draft"
        updates["updated_at"] = _now()
        columns = ", ".join(f"{key} = ?" for key in updates)
        with self._lock, self._conn:
            self._conn.execute(
                f"""
                UPDATE email_drafts
                SET {columns}, version = version + 1
                WHERE id = ?
                """,
                (*updates.values(), draft_id),
            )
        return self.get_draft(draft_id)

    def set_draft_content(
        self,
        draft_id: str,
        *,
        subject: str,
        body: str,
        source_email_id: str = "",
    ) -> dict[str, Any]:
        with self._lock, self._conn:
            cursor = self._conn.execute(
                """
                UPDATE email_drafts
                SET subject = ?, body = ?, source_email_id = ?, status = 'draft',
                    version = version + 1, updated_at = ?
                WHERE id = ?
                """,
                (subject, body, source_email_id, _now(), draft_id),
            )
        if cursor.rowcount != 1:
            raise KeyError(draft_id)
        return self.get_draft(draft_id)

    def set_draft_status(self, draft_id: str, status: str) -> dict[str, Any]:
        with self._lock, self._conn:
            cursor = self._conn.execute(
                """
                UPDATE email_drafts SET status = ?, updated_at = ?
                WHERE id = ?
                """,
                (status, _now(), draft_id),
            )
        if cursor.rowcount != 1:
            raise KeyError(draft_id)
        return self.get_draft(draft_id)

    @staticmethod
    def _draft_row(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "lead_id": row["lead_id"],
            "campaign_id": row["campaign_id"],
            "subject": row["subject"],
            "body": row["body"],
            "status": row["status"],
            "version": row["version"],
            "updated_at": row["updated_at"],
        }

    # Lead searches

    def create_search(
        self,
        query: str,
        filters: dict[str, Any],
        result_limit: int,
    ) -> dict[str, Any]:
        search_id = str(uuid4())
        now = _now()
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO lead_searches (
                    id, query, filters, result_limit, status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, 'queued', ?, ?)
                """,
                (search_id, query, _json(filters), result_limit, now, now),
            )
        return self.get_search(search_id)

    def finish_search(
        self,
        search_id: str,
        results: list[dict[str, Any]],
    ) -> dict[str, Any]:
        now = _now()
        with self._lock, self._conn:
            self._conn.execute(
                "DELETE FROM lead_search_results WHERE search_id = ?", (search_id,)
            )
            self._conn.executemany(
                """
                INSERT INTO lead_search_results (search_id, lead_id, payload)
                VALUES (?, ?, ?)
                """,
                [
                    (search_id, result["id"], _json(result))
                    for result in results
                    if result.get("id")
                ],
            )
            self._conn.execute(
                """
                UPDATE lead_searches
                SET status = 'succeeded', found_count = ?, error = '', updated_at = ?
                WHERE id = ?
                """,
                (len(results), now, search_id),
            )
        return self.get_search(search_id)

    def fail_search(self, search_id: str, error: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """
                UPDATE lead_searches
                SET status = 'failed', error = ?, updated_at = ? WHERE id = ?
                """,
                (error[:4000], _now(), search_id),
            )

    def list_searches(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM lead_searches ORDER BY created_at DESC"
            ).fetchall()
        return [self._search_summary(row) for row in rows]

    def get_search(self, search_id: str) -> dict[str, Any]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM lead_searches WHERE id = ?", (search_id,)
            ).fetchone()
            results = self._conn.execute(
                """
                SELECT payload, imported FROM lead_search_results
                WHERE search_id = ? ORDER BY rowid
                """,
                (search_id,),
            ).fetchall()
        if row is None:
            raise KeyError(search_id)
        payload = self._search_summary(row)
        payload["filters"] = json.loads(row["filters"] or "{}")
        payload["error"] = row["error"]
        payload["leads"] = [
            {**json.loads(item["payload"]), "imported": bool(item["imported"])}
            for item in results
        ]
        return payload

    def search_results(
        self,
        search_id: str,
        lead_ids: list[str],
    ) -> list[dict[str, Any]]:
        if not lead_ids:
            return []
        placeholders = ", ".join("?" for _ in lead_ids)
        with self._lock:
            rows = self._conn.execute(
                f"""
                SELECT lead_id, payload FROM lead_search_results
                WHERE search_id = ? AND lead_id IN ({placeholders})
                """,
                (search_id, *lead_ids),
            ).fetchall()
        return [json.loads(row["payload"]) for row in rows]

    def mark_search_results_imported(self, search_id: str, lead_ids: list[str]) -> None:
        if not lead_ids:
            return
        placeholders = ", ".join("?" for _ in lead_ids)
        with self._lock, self._conn:
            self._conn.execute(
                f"""
                UPDATE lead_search_results SET imported = 1
                WHERE search_id = ? AND lead_id IN ({placeholders})
                """,
                (search_id, *lead_ids),
            )

    @staticmethod
    def _search_summary(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "query": row["query"],
            "status": row["status"],
            "found_count": row["found_count"],
            "created_at": row["created_at"],
        }

    # Mailbox

    def add_mailbox_message(
        self,
        *,
        message_id: str,
        thread_id: str,
        lead_id: str,
        direction: str,
        from_addr: str,
        to_addr: str,
        subject: str,
        body: str,
        sent_at: str | None = None,
        bounced: bool = False,
    ) -> None:
        sent_at = sent_at or _now()
        with self._lock, self._conn:
            inserted = self._conn.execute(
                """
                INSERT OR IGNORE INTO mailbox_messages (
                    id, thread_id, direction, from_addr, to_addr, subject, body, sent_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    message_id,
                    thread_id,
                    direction,
                    from_addr,
                    to_addr,
                    subject,
                    body,
                    sent_at,
                ),
            )
            if inserted.rowcount == 0:
                if bounced:
                    self._conn.execute(
                        """
                        UPDATE mailbox_threads
                        SET bounced = 1, updated_at = MAX(updated_at, ?)
                        WHERE id = ?
                        """,
                        (sent_at, thread_id),
                    )
                return
            existing = self._conn.execute(
                "SELECT unread, replied FROM mailbox_threads WHERE id = ?",
                (thread_id,),
            ).fetchone()
            unread = 1 if direction == "in" else int(existing["unread"]) if existing else 0
            has_inbound = bool(
                self._conn.execute(
                    """
                    SELECT 1 FROM mailbox_messages
                    WHERE thread_id = ? AND direction = 'in' LIMIT 1
                    """,
                    (thread_id,),
                ).fetchone()
            )
            replied = (
                1
                if direction == "out" and has_inbound
                else int(existing["replied"]) if existing else 0
            )
            self._conn.execute(
                """
                INSERT INTO mailbox_threads (
                    id, subject, lead_id, last_from, unread, bounced, replied, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    subject = excluded.subject,
                    last_from = excluded.last_from,
                    unread = excluded.unread,
                    bounced = MAX(mailbox_threads.bounced, excluded.bounced),
                    replied = excluded.replied,
                    updated_at = excluded.updated_at
                """,
                (
                    thread_id,
                    subject,
                    lead_id,
                    from_addr,
                    unread,
                    int(bounced),
                    replied,
                    sent_at,
                ),
            )

    def list_mailbox_threads(
        self,
        folder: str,
        page: int,
        page_size: int,
    ) -> dict[str, Any]:
        conditions = {
            "inbox": (
                "EXISTS (SELECT 1 FROM mailbox_messages m "
                "WHERE m.thread_id = t.id AND m.direction = 'in')"
            ),
            "sent": (
                "EXISTS (SELECT 1 FROM mailbox_messages m "
                "WHERE m.thread_id = t.id AND m.direction = 'out')"
            ),
            "replied": "t.replied = 1",
            "bounced": "t.bounced = 1",
        }
        where = conditions[folder]
        offset = (page - 1) * page_size
        with self._lock:
            total = self._conn.execute(
                f"SELECT COUNT(*) n FROM mailbox_threads t WHERE {where}"
            ).fetchone()["n"]
            rows = self._conn.execute(
                f"""
                SELECT t.*,
                    COALESCE((
                        SELECT substr(m.body, 1, 240)
                        FROM mailbox_messages m
                        WHERE m.thread_id = t.id
                        ORDER BY m.sent_at DESC LIMIT 1
                    ), '') AS last_snippet
                FROM mailbox_threads t
                WHERE {where}
                ORDER BY t.updated_at DESC
                LIMIT ? OFFSET ?
                """,
                (page_size, offset),
            ).fetchall()
        return {
            "items": [
                {
                    "id": row["id"],
                    "subject": row["subject"],
                    "lead_id": row["lead_id"],
                    "last_from": row["last_from"],
                    "last_snippet": row["last_snippet"],
                    "unread": bool(row["unread"]),
                    "updated_at": row["updated_at"],
                }
                for row in rows
            ],
            "total": total,
        }

    def get_mailbox_thread(self, thread_id: str) -> dict[str, Any]:
        with self._lock:
            thread = self._conn.execute(
                "SELECT * FROM mailbox_threads WHERE id = ?", (thread_id,)
            ).fetchone()
            messages = self._conn.execute(
                """
                SELECT * FROM mailbox_messages
                WHERE thread_id = ? ORDER BY sent_at
                """,
                (thread_id,),
            ).fetchall()
        if thread is None:
            raise KeyError(thread_id)
        return {
            "id": thread["id"],
            "subject": thread["subject"],
            "lead_id": thread["lead_id"],
            "messages": [
                {
                    "id": row["id"],
                    "direction": row["direction"],
                    "from": row["from_addr"],
                    "to": row["to_addr"],
                    "subject": row["subject"],
                    "body": row["body"],
                    "sent_at": row["sent_at"],
                }
                for row in messages
            ],
        }

    def mark_thread_read(self, thread_id: str) -> None:
        with self._lock, self._conn:
            cursor = self._conn.execute(
                "UPDATE mailbox_threads SET unread = 0 WHERE id = ?", (thread_id,)
            )
        if cursor.rowcount != 1:
            raise KeyError(thread_id)

    # Natural-language orchestrator conversations

    def create_conversation(self, message: str) -> str:
        conversation_id = str(uuid4())
        now = _now()
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO operator_conversations (id, created_at, updated_at)
                VALUES (?, ?, ?)
                """,
                (conversation_id, now, now),
            )
            self._conn.execute(
                """
                INSERT INTO operator_messages (
                    id, conversation_id, role, content, created_at
                ) VALUES (?, ?, 'user', ?, ?)
                """,
                (str(uuid4()), conversation_id, message, now),
            )
        return conversation_id

    def add_operator_message(
        self,
        conversation_id: str,
        role: str,
        content: str,
        tool_calls: list[dict[str, Any]] | None = None,
    ) -> None:
        now = _now()
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO operator_messages (
                    id, conversation_id, role, content, tool_calls, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid4()),
                    conversation_id,
                    role,
                    content,
                    _json(tool_calls) if tool_calls else None,
                    now,
                ),
            )
            self._conn.execute(
                "UPDATE operator_conversations SET updated_at = ? WHERE id = ?",
                (now, conversation_id),
            )

    def add_timeline_step(
        self,
        conversation_id: str,
        *,
        step: str,
        agent: str,
        action: str,
        status: str,
        started_at: str | None = None,
        finished_at: str | None = None,
    ) -> int:
        with self._lock, self._conn:
            cursor = self._conn.execute(
                """
                INSERT INTO operator_timeline (
                    conversation_id, step, agent, action, status, started_at, finished_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    conversation_id,
                    step,
                    agent,
                    action,
                    status,
                    started_at,
                    finished_at,
                ),
            )
        return int(cursor.lastrowid)

    def update_timeline_step(self, timeline_id: int, status: str) -> None:
        finished_at = _now() if status in {"succeeded", "failed"} else None
        with self._lock, self._conn:
            self._conn.execute(
                """
                UPDATE operator_timeline
                SET status = ?, started_at = COALESCE(started_at, ?), finished_at = ?
                WHERE id = ?
                """,
                (status, _now(), finished_at, timeline_id),
            )

    def list_conversations(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT c.id, c.created_at, c.updated_at,
                    COALESCE((
                        SELECT content FROM operator_messages m
                        WHERE m.conversation_id = c.id
                        ORDER BY m.created_at DESC LIMIT 1
                    ), '') AS last_message
                FROM operator_conversations c
                ORDER BY c.updated_at DESC
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def get_conversation(self, conversation_id: str) -> dict[str, Any]:
        with self._lock:
            conversation = self._conn.execute(
                "SELECT * FROM operator_conversations WHERE id = ?",
                (conversation_id,),
            ).fetchone()
            messages = self._conn.execute(
                """
                SELECT role, content, tool_calls, created_at
                FROM operator_messages
                WHERE conversation_id = ? ORDER BY created_at
                """,
                (conversation_id,),
            ).fetchall()
        if conversation is None:
            raise KeyError(conversation_id)
        return {
            "id": conversation["id"],
            "messages": [
                {
                    "role": row["role"],
                    "content": row["content"],
                    "tool_calls": (
                        json.loads(row["tool_calls"]) if row["tool_calls"] else None
                    ),
                    "created_at": row["created_at"],
                }
                for row in messages
            ],
        }

    def conversation_timeline(self, conversation_id: str) -> list[dict[str, Any]]:
        self.get_conversation(conversation_id)
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT step, agent, action, status, started_at, finished_at
                FROM operator_timeline
                WHERE conversation_id = ? ORDER BY id
                """,
                (conversation_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def close(self) -> None:
        with self._lock:
            self._conn.close()
