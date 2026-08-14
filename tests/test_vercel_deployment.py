from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.signed_auth import SignedAuthConfig, SignedAuthService
from backend.auth import AuthConfig
from backend.data_source import DataSourceSettings
from backend.quota import DemoQuotaConfig
from env_settings import runtime_dir, validate_vercel_environment
from model_provider import allow_mock_model
from mcp_router import MCPExecutionRequest, PostgresExecutorConfig, PostgresQueryExecutor
from sql_generation import (
    CoderModelClient,
    CoderModelConfig,
    normalize_sql_for_dialect,
)


class VercelDeploymentContractTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(__file__).resolve().parents[1]

    def test_vercel_uses_native_fastapi_routing_and_allows_five_minutes(self):
        config = json.loads((self.root / "vercel.json").read_text(encoding="utf-8"))
        self.assertEqual(config["functions"]["app.py"]["maxDuration"], 300)
        self.assertEqual(config["functions"]["api/index.py"]["maxDuration"], 300)
        self.assertIn("tests/**", config["functions"]["api/index.py"]["excludeFiles"])
        self.assertNotIn("rewrites", config)
        self.assertTrue((self.root / "app.py").is_file())
        self.assertEqual((self.root / ".python-version").read_text().strip(), "3.12")

    def test_postgres_runtime_dependencies_and_secret_placeholders_exist(self):
        requirements = (self.root / "requirements.txt").read_text(encoding="utf-8")
        example = (self.root / ".env.example").read_text(encoding="utf-8")
        self.assertIn("psycopg[binary]", requirements)
        self.assertNotIn("-r requirements-core.txt", requirements)
        self.assertIn("ASKDATA_POSTGRES_URL", example)
        self.assertIn("ASKDATA_SESSION_SECRET", example)
        self.assertNotIn("postgresql://neondb_owner:", example)

    def test_blank_optional_values_use_safe_vercel_defaults(self):
        env = {
            "VERCEL": "1",
            "ASKDATA_POSTGRES_URL": "postgresql://demo:secret@example.test/db",
            "ASKDATA_DATABASE_TYPE": "",
            "ASKDATA_RUNTIME_DIR": "",
            "ASKDATA_SESSION_TTL_SECONDS": "",
            "ASKDATA_SCHEMA_SAMPLE_SIZE": "",
            "ASKDATA_GUEST_QUERY_LIMIT": "",
        }
        with patch.dict(os.environ, env, clear=True):
            self.assertEqual(DataSourceSettings.from_environment().database_type, "postgres")
            self.assertEqual(DataSourceSettings.from_environment().schema_sample_size, 0)
            self.assertEqual(AuthConfig.from_environment("ignored").session_ttl_seconds, 86_400)
            self.assertEqual(DemoQuotaConfig.from_environment("ignored").query_limit, 2)
            self.assertEqual(runtime_dir(self.root), Path("/tmp/askdata_runtime"))

    def test_vercel_validation_reports_issues_without_crashing_web_startup(self):
        with patch.dict(os.environ, {"VERCEL": "1"}, clear=True):
            issues = validate_vercel_environment()
        message = "；".join(issues)
        for name in (
            "ASKDATA_POSTGRES_URL",
            "DEEPSEEK_API_KEY",
        ):
            self.assertIn(name, message)
        self.assertNotIn("ASKDATA_SESSION_SECRET", message)

    def test_complete_vercel_environment_passes_validation(self):
        env = {
            "VERCEL": "1",
            "ASKDATA_POSTGRES_URL": "postgresql://demo:secret@example.test/db",
            "ASKDATA_LLM_PROVIDER": "deepseek",
            "DEEPSEEK_API_KEY": "test-key",
            "ASKDATA_BOOTSTRAP_EMAIL": "demo@example.com",
            "ASKDATA_BOOTSTRAP_PASSWORD": "safe-password",
            "ASKDATA_SESSION_SECRET": "s" * 32,
        }
        with patch.dict(os.environ, env, clear=True):
            validate_vercel_environment()

    def test_blank_postgres_numeric_values_use_defaults(self):
        from mcp_router import PostgresExecutorConfig

        env = {
            "ASKDATA_POSTGRES_URL": "postgresql://demo:secret@example.test/db",
            "ASKDATA_POSTGRES_CONNECT_TIMEOUT": "",
            "ASKDATA_POSTGRES_STATEMENT_TIMEOUT_MS": "",
            "ASKDATA_POSTGRES_MAX_ROWS": "",
        }
        with patch.dict(os.environ, env, clear=True):
            config = PostgresExecutorConfig.from_env()
        self.assertEqual(config.connect_timeout, 10)
        self.assertEqual(config.statement_timeout_ms, 30_000)
        self.assertEqual(config.max_rows, 5000)

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

    def test_public_guest_session_needs_no_shared_password(self):
        config = SignedAuthConfig(
            email="guest@askdata.demo",
            password="",
            display_name="问数访客",
            secret="g" * 32,
            session_ttl_seconds=3600,
        )
        service = SignedAuthService(config)
        user, token = service.issue_guest()
        restored = service.get_user_for_token(token)
        self.assertEqual(restored.id, user.id)
        self.assertFalse(restored.is_admin)
        with self.assertRaises(Exception):
            service.login(email=config.email, password="", source="test")

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

    def test_postgres_two_argument_round_casts_aggregate_to_numeric(self):
        sql = """SELECT
    TO_CHAR(order_date, 'YYYY-MM') AS sales_month,
    ROUND(SUM(sales_amount), 2) AS sales_amount
FROM orders
GROUP BY TO_CHAR(order_date, 'YYYY-MM');"""
        normalized = normalize_sql_for_dialect(sql, "postgres")
        compact = " ".join(normalized.upper().split())
        self.assertIn("ROUND(CAST(SUM(SALES_AMOUNT) AS DECIMAL), 2)", compact)
        self.assertIn("TO_CHAR(ORDER_DATE, 'YYYY-MM')", compact)

    def test_postgres_round_without_digits_is_not_rewritten(self):
        sql = "SELECT ROUND(sales_amount) FROM orders"
        self.assertEqual(normalize_sql_for_dialect(sql, "postgres"), sql)

    def test_non_postgres_round_is_not_rewritten(self):
        sql = "SELECT ROUND(SUM(sales_amount), 2) FROM orders"
        self.assertEqual(normalize_sql_for_dialect(sql, "sqlite"), sql)

    def test_postgres_executor_normalizes_month_comparison_before_execution(self):
        executed = []

        class FakeCursor:
            description = []

            def __init__(self, rows=None):
                self._rows = rows or []

            def fetchmany(self, _limit):
                return list(self._rows)

        class FakeTransaction:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, traceback):
                return False

        class FakeConnection:
            def transaction(self):
                return FakeTransaction()

            def execute(self, sql, params=None):
                executed.append((sql, params))
                if "sales_month" in sql:
                    return FakeCursor(
                        [
                            {"sales_month": "2026-07", "sales_amount": 100.25},
                            {"sales_month": "2026-08", "sales_amount": 120.75},
                        ]
                    )
                return FakeCursor()

            def close(self):
                pass

        sql = """SELECT
            TO_CHAR(order_date, 'YYYY-MM') AS sales_month,
            ROUND(SUM(sales_amount), 2) AS sales_amount
        FROM orders
        GROUP BY TO_CHAR(order_date, 'YYYY-MM')
        ORDER BY sales_month"""
        executor = PostgresQueryExecutor(
            PostgresExecutorConfig(database_url="postgresql://example.test/db"),
            database_alias="trade_db",
        )
        with patch("psycopg.connect", return_value=FakeConnection()):
            result = executor.execute(MCPExecutionRequest(database="trade_db", sql=sql))

        self.assertTrue(result.success, result.error)
        self.assertEqual(result.row_count, 2)
        compact = " ".join(result.sql.upper().split())
        self.assertIn("ROUND(CAST(SUM(SALES_AMOUNT) AS DECIMAL), 2)", compact)
        self.assertEqual(executed[-1][0], result.sql)


if __name__ == "__main__":
    unittest.main()
