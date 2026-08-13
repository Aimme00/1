from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.signed_auth import SignedAuthConfig, SignedAuthService
from model_provider import allow_mock_model
from sql_generation import CoderModelClient, CoderModelConfig


class VercelDeploymentContractTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(__file__).resolve().parents[1]

    def test_vercel_routes_everything_to_fastapi_and_allows_five_minutes(self):
        config = json.loads((self.root / "vercel.json").read_text(encoding="utf-8"))
        self.assertEqual(config["functions"]["api/index.py"]["maxDuration"], 300)
        self.assertIn("tests/**", config["functions"]["api/index.py"]["excludeFiles"])
        self.assertEqual(config["rewrites"][0]["destination"], "/api/index")
        self.assertEqual((self.root / ".python-version").read_text().strip(), "3.12")

    def test_postgres_runtime_dependencies_and_secret_placeholders_exist(self):
        requirements = (self.root / "requirements-core.txt").read_text(encoding="utf-8")
        example = (self.root / ".env.example").read_text(encoding="utf-8")
        self.assertIn("psycopg[binary]", requirements)
        self.assertIn("ASKDATA_POSTGRES_URL", example)
        self.assertIn("ASKDATA_SESSION_SECRET", example)
        self.assertNotIn("postgresql://neondb_owner:", example)

    def test_signed_login_survives_new_service_instance_and_detects_tampering(self):
        config = SignedAuthConfig(
            email="interview@example.com",
            password="safe-password",
            display_name="Interview Demo",
            secret="a" * 32,
            session_ttl_seconds=3600,
        )
        first = SignedAuthService(config)
        user, token = first.login(email=config.email, password=config.password)
        second = SignedAuthService(config)
        self.assertEqual(second.get_user_for_token(token), user)
        self.assertIsNone(second.get_user_for_token(token + "x"))
        another_user, _ = second.login(email=config.email, password=config.password)
        self.assertNotEqual(another_user.id, user.id)

    def test_postgres_mock_sql_uses_postgres_date_functions(self):
        with patch.dict("os.environ", {}, clear=True):
            client = CoderModelClient(CoderModelConfig(use_mock_when_no_api_key=True))
        prompt = """要求SQL需要符合postgres语法。
操作指令：先筛选最近30天，然后按日期汇总
输出目标：order_date，sales_amount
Schema: orders.order_date orders.sales_amount"""
        sql = client.generate_sql(prompt)
        self.assertIn("CURRENT_DATE - INTERVAL '29 days'", sql)
        self.assertNotIn("date('now'", sql)


if __name__ == "__main__":
    unittest.main()
