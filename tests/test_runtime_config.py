from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.runtime_paths import agent_output_dir, orchestrator_output_dir
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


if __name__ == "__main__":
    unittest.main()
