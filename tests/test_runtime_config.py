from __future__ import annotations

import importlib
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from core.runtime_paths import agent_output_dir, orchestrator_output_dir
from orchestrator.adapters.live import _load_agent
from orchestrator.config import OrchestratorConfig


class RuntimeConfigTests(unittest.TestCase):
    def test_data_root_redirects_all_runtime_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir, patch.dict(
            os.environ, {"AUTOREACH_DATA_DIR": tempdir}
        ):
            root = Path(tempdir).resolve()
            self.assertEqual(orchestrator_output_dir(), root / "orchestrator")
            self.assertEqual(
                agent_output_dir("agent3-email-writer"),
                root / "agents" / "agent3-email-writer",
            )

    def test_orchestrator_reads_safe_environment_controls(self) -> None:
        with patch.dict(
            os.environ,
            {
                "AUTOREACH_SIMULATE": "false",
                "AUTOREACH_REPLY_HANDLING_ENABLED": "true",
                "AUTOREACH_EMAILS_PER_DAY": "7",
                "AUTOREACH_LLM_PROVIDER": "openrouter",
                "AUTOREACH_LLM_MODEL": "anthropic/claude-sonnet-4.6",
                "AUTOREACH_LLM_MAX_TOKENS": "1600",
            },
            clear=False,
        ):
            config = OrchestratorConfig.from_env()
        self.assertFalse(config.simulate)
        self.assertTrue(config.reply_handling_enabled)
        self.assertEqual(config.volume.emails_per_day, 7)
        self.assertEqual(config.campaign_model.provider, "openrouter")
        self.assertEqual(config.campaign_model.model, "anthropic/claude-sonnet-4.6")
        self.assertEqual(config.campaign_model.max_tokens, 1600)


class RedisConfigurationTests(unittest.IsolatedAsyncioTestCase):
    async def test_unavailable_redis_reports_api_setting_remediation(self) -> None:
        _load_agent("agent1-lead-finder", "agent1_lead_finder")
        redis_store = importlib.import_module(
            "agent1_lead_finder.storage.redis_store"
        )
        client = SimpleNamespace(
            ping=AsyncMock(side_effect=redis_store.RedisError("unavailable")),
            aclose=AsyncMock(),
        )

        with patch.object(
            redis_store.aioredis,
            "from_url",
            AsyncMock(return_value=client),
        ):
            store = redis_store.RedisStore("redis://redis:6379/0")
            with self.assertRaisesRegex(
                ConnectionError,
                r"set redis_url with PATCH /v1/settings",
            ):
                await store.connect()

        client.aclose.assert_awaited_once()
        self.assertIsNone(store._client)


if __name__ == "__main__":
    unittest.main()
