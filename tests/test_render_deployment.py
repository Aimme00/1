from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.quota import DemoQuotaConfig, DemoQuotaExceededError, DemoQuotaService
from model_provider import allow_mock_model


class RenderDeploymentTestCase(unittest.TestCase):
    def test_render_blueprint_is_free_and_keeps_secrets_out_of_git(self) -> None:
        text = (Path(__file__).resolve().parents[1] / "render.yaml").read_text(
            encoding="utf-8"
        )
        self.assertIn("plan: free", text)
        self.assertIn("healthCheckPath: /health", text)
        self.assertIn("DEEPSEEK_API_KEY\n        sync: false", text)
        self.assertNotIn("sk-", text)

    def test_production_disables_mock_model_by_default(self) -> None:
        with patch.dict("os.environ", {"ASKDATA_ENV": "production"}, clear=True):
            self.assertFalse(allow_mock_model())
        with patch.dict(
            "os.environ",
            {"ASKDATA_ENV": "production", "ASKDATA_ALLOW_MOCK_MODEL": "true"},
            clear=True,
        ):
            self.assertTrue(allow_mock_model())

    def test_demo_quota_is_atomic_and_tester_token_bypasses_limit(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = DemoQuotaService(
                DemoQuotaConfig(
                    db_path=Path(temp_dir) / "quota.db",
                    query_limit=2,
                    tester_token="owner-test-token",
                )
            )
            self.assertEqual(service.consume(subject="203.0.113.8")["remaining"], 1)
            self.assertEqual(service.consume(subject="203.0.113.8")["remaining"], 0)
            with self.assertRaises(DemoQuotaExceededError):
                service.consume(subject="203.0.113.8")
            self.assertTrue(
                service.consume(
                    subject="203.0.113.8", tester_token="owner-test-token"
                )["unlimited"]
            )

if __name__ == "__main__":
    unittest.main()
