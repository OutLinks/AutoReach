"""
Sending Layer orchestrator.

Owns the provider registry and routes each outgoing message to the configured
provider (Instantly / Gmail / Outreach / SES / SMTP). The provider is chosen per
account (an account declares its provider) falling back to the global default.

This layer is delivery only — scheduling decided *when* and *which account*,
tracking will instrument the body, and the agent persists the SentEmail record.
"""

from __future__ import annotations

import logging
from typing import Optional

from ...config import ServiceConfig
from ...models import SendingAccount, SendResult
from .base import OutgoingMessage, SendingProvider
from .instantly import InstantlyProvider
from .gmail import GmailProvider
from .outreach import OutreachProvider
from .ses import SesProvider
from .smtp import SmtpProvider

logger = logging.getLogger(__name__)

_PROVIDERS: dict[str, type[SendingProvider]] = {
    "instantly": InstantlyProvider,
    "gmail": GmailProvider,
    "outreach": OutreachProvider,
    "ses": SesProvider,
    "smtp": SmtpProvider,
}


class SendingLayer:
    def __init__(self, config: ServiceConfig) -> None:
        self._config = config
        # Instantiate one provider per known type, reused across sends.
        self._providers: dict[str, SendingProvider] = {
            name: cls(config) for name, cls in _PROVIDERS.items()
        }
        if config.provider not in self._providers:
            logger.warning(
                "SendingLayer: unknown default provider %r, falling back to smtp",
                config.provider,
            )

    async def deliver(
        self,
        *,
        account: SendingAccount,
        to_email: str,
        subject: str,
        body: str,
        from_name: str = "",
        reply_to: str = "",
        in_reply_to: str = "",
        references: str = "",
    ) -> SendResult:
        """Build an OutgoingMessage and hand it to the right provider."""
        provider = self._provider_for(account)
        message = OutgoingMessage(
            to_email=to_email,
            from_email=account.email,
            from_name=from_name or account.display_name or account.email,
            subject=subject,
            body=body,
            reply_to=reply_to,
            in_reply_to=in_reply_to,
            references=references,
        )
        result = await provider.send(message)
        logger.info(
            "SendingLayer: %s via %s → %s (%s)",
            to_email, provider.name, "ok" if result.success else "FAIL", result.status,
        )
        return result

    def _provider_for(self, account: SendingAccount) -> SendingProvider:
        name = account.provider or self._config.provider
        return self._providers.get(name) or self._providers["smtp"]
