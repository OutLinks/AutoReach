"""
Input Layer orchestrator.

Turns raw reply hand-offs into fully-contextualized IncomingReply objects:

  1. reply_reader        → load pending hand-offs from Agent 4 (or a webhook),
  2. message_parser      → strip quotes/signatures to the lead's actual words,
  3. conversation_loader → attach original email, lead score, prior-exchange count.

The output is ready for the understanding layer.
"""

from __future__ import annotations

import logging

from ...config import ServiceConfig
from ...models import IncomingReply
from ...storage.conversation_store import ConversationStore
from . import message_parser
from .reply_reader import ReplyReader
from .conversation_loader import ConversationLoader

logger = logging.getLogger(__name__)


class InputLayer:
    def __init__(self, config: ServiceConfig, store: ConversationStore) -> None:
        self._reader = ReplyReader(config.replies_dir)
        self._loader = ConversationLoader(config.emails_db_path, store)

    def collect(self, mark_done: bool = True) -> list[IncomingReply]:
        """Read, parse, and enrich every pending reply."""
        return [self._prepare(r) for r in self._reader.read_pending(mark_done)]

    def prepare_payload(self, payload: dict) -> IncomingReply:
        """Prepare a single reply delivered directly (e.g. webhook)."""
        return self._prepare(ReplyReader.from_payload(payload))

    def _prepare(self, reply: IncomingReply) -> IncomingReply:
        reply.clean_body = message_parser.parse(reply.raw_body)
        reply = self._loader.enrich(reply)
        logger.debug(
            "InputLayer: prepared reply from lead %s (%d prior exchanges)",
            reply.lead_id, reply.prior_exchanges,
        )
        return reply
