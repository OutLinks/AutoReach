"""
Sending-account loader.

Loads the pool of SendingAccount identities used for delivery + rotation.

Load order:
  1. JSON file at AGENT4_ACCOUNTS_FILE (a list of account objects),
  2. a single account from SENDER_EMAIL (+ SENDER_FROM_NAME),
  3. a simulated default account (only when config.simulate is True),
  4. otherwise raise — you can't send without an account.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from .config import ServiceConfig
from .models import SendingAccount

logger = logging.getLogger(__name__)


def load_accounts(config: ServiceConfig) -> list[SendingAccount]:
    accounts = _from_file() or _from_env(config)
    if accounts:
        return accounts

    if config.simulate:
        logger.warning("load_accounts: no accounts configured — using a simulated default")
        return [
            SendingAccount(
                email="outreach@autoreach.local",
                provider=config.provider,
                display_name="AutoReach",
                daily_limit=config.daily_send_limit,
                hourly_limit=config.hourly_send_limit,
                warmup_start_date=datetime.now(timezone.utc),
            )
        ]

    raise ValueError(
        "No sending accounts configured. Set AGENT4_ACCOUNTS_FILE or SENDER_EMAIL, "
        "or enable simulate mode."
    )


def _from_file() -> list[SendingAccount]:
    path_str = os.getenv("AGENT4_ACCOUNTS_FILE", "")
    if not path_str:
        return []
    path = Path(path_str)
    if not path.exists():
        logger.warning("load_accounts: accounts file not found at %s", path)
        return []
    try:
        data = json.loads(path.read_text())
        return [SendingAccount(**item) for item in data]
    except Exception as exc:
        logger.error("load_accounts: failed to parse %s — %s", path, exc)
        return []


def _from_env(config: ServiceConfig) -> list[SendingAccount]:
    email = os.getenv("SENDER_EMAIL", "")
    if not email:
        return []
    return [
        SendingAccount(
            email=email,
            provider=config.provider,
            display_name=os.getenv("SENDER_FROM_NAME", ""),
            daily_limit=config.daily_send_limit,
            hourly_limit=config.hourly_send_limit,
            warmup_start_date=datetime.now(timezone.utc),
        )
    ]
