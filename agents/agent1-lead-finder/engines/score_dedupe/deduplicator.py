"""
Deduplication logic.

Checks each incoming lead against the global Redis seen-sets.
A lead is a duplicate if ANY of its identifying keys has been seen before:
  1. Email (primary — strongest signal)
  2. LinkedIn URL
  3. Company name + full name composite key

Unique leads are immediately registered in Redis so subsequent leads in the
same batch (or a future run) will correctly detect them as duplicates.
"""

from __future__ import annotations

import logging

from ...models import Lead
from ...storage import RedisStore

logger = logging.getLogger(__name__)


def _name_key(lead: Lead) -> str | None:
    """Build a composite dedup key from company + name."""
    company = (lead.company_name or "").strip().lower()
    name = (lead.full_name or f"{lead.first_name or ''} {lead.last_name or ''}").strip().lower()
    if company and name:
        return f"{company}::{name}"
    return None


class Deduplicator:
    """Filters a lead list to unique contacts using Redis as the store."""

    def __init__(self, store: RedisStore) -> None:
        self._store = store

    async def dedupe(self, leads: list[Lead]) -> list[Lead]:
        """
        Returns only unique leads. Registers each unique lead in Redis.
        Duplicate leads are marked `is_duplicate = True` and excluded.
        """
        unique: list[Lead] = []
        duplicate_count = 0

        for lead in leads:
            email = lead.email or None
            linkedin = lead.linkedin_url or None
            name_key = _name_key(lead)

            is_dup = await self._store.is_duplicate(email, linkedin, name_key)

            if is_dup:
                lead.is_duplicate = True
                duplicate_count += 1
                logger.debug(
                    "Duplicate skipped: %s <%s>",
                    lead.full_name or lead.company_name, lead.email,
                )
                continue

            # Register in global seen-sets
            await self._store.register_lead(email, linkedin, name_key)
            lead.stage = "deduped"
            unique.append(lead)

        logger.info(
            "Deduplicator: %d in  %d unique  %d duplicates",
            len(leads), len(unique), duplicate_count,
        )
        return unique
