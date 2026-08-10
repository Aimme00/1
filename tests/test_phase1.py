from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from askdata_pipeline import (
    AgentRunStatus,
    AgentState,
    AskDataText2SQLPipeline,
    PipelineConfig,
)
from mcp_router import (
    MCPExecutionRequest,
    MySQLExecutorConfig,
    SQLiteMCPExecutor,
)
from sql_validation import (
    SQLValidationRepairLoop,
    SQLValidator,
    SQLValidatorConfig,
)
from sql_generation import SqlGenerationResult


class AgentStateTestCase(unittest.TestCase):
    def test_state_serializes_for_api_use(self) -> None:
        state = AgentState(
            query="统计销售额",
            user_id="u1",
            session_id="s1",
            status=AgentRunStatus.RUNNING,
        )
        payload = state.to_dict()
        self.assertTrue(payload["run_id"].startswith("run_"))
        self.assertEqual(payload["status"], "running")
        self.assertEqual(payload["query"], "统计销售额")
        self.assertEqual(payload["warnings"], [])


class SQLValidatorTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.validator = SQLValidator(
            SQLValidatorConfig(
                dialect="sqlite",
                max_rows=100,
                require_sqlglot=False,
            )
        )

    def test_readonly_query_is_limited(self) -> None:
        result = self.validator.validate(
            "SELECT interest_rate FROM interest_info;",
            allowed_tables={"interest_info"},
            allowed_columns={"interest_info": {"interest_rate"}},
        )
        self.assertTrue(result.is_valid, result.feedback_text())
        self.assertIn("LIMIT 100", result.validated_sql.upper())

    def test_multiple_statements_are_rejected(self) -> None:
        result = self.validator.validate(
            "SELECT 1; DROP TABLE interest_info;",
            allowed_tables={"interest_info"},
        )
        self.assertFalse(result.is_valid)
        self.assertIn("multiple_statements", [issue.code for issue in result.errors])

    def test_write_keyword_inside_cte_is_rejected(self) -> None:
        result = self.validator.validate(
            "WITH changed AS (DELETE FROM interest_info RETURNING *) SELECT * FROM changed",
            allowed_tables={"interest_info"},
        )
        self.assertFalse(result.is_valid)
        self.assertIn("forbidden_keyword", [issue.code for issue in result.errors])

    def test_unapproved_table_is_rejected(self) -> None:
        result = self.validator.validate(
            "SELECT secret FROM private_table",
            allowed_tables={"interest_info"},
        )
        self.assertFalse(result.is_valid)
        self.assertIn("table_not_allowed", [issue.code for issue in result.errors])

    def test_production_mode_fails_closed_without_sqlglot(self) -> None:
        validator = SQLValidator(
            SQLValidatorConfig(require_sqlglot=True)
        )
        result = validator.validate("SELECT 1")
        try:
            import sqlglot  # noqa: F401
        except ImportError:
            self.assertFalse(result.is_valid)
            self.assertEqual(result.errors[0].code, "sqlglot_unavailable")
        else:
            self.assertTrue(result.is_valid, result.feedback_text())

    def test_repair_loop_uses_validation_feedback(self) -> None:
        feedbacks: list[str] = []

        def repair(previous_sql: str, feedback: str, attempt: int) -> str:
            feedbacks.append(feedback)
            return "SELECT interest_rate FROM interest_info"

        outcome = SQLValidationRepairLoop(
            self.validator,
            max_repair_attempts=2,
        ).run(
            "DELETE FROM interest_info",
            repair=repair,
            allowed_tables={"interest_info"},
        )
        self.assertTrue(outcome.success)
        self.assertEqual(len(outcome.attempts), 2)
        self.assertIn("DELETE", feedbacks[0])


class ExecutorTestCase(unittest.TestCase):
    def test_sqlite_executor_is_readonly_and_truncates(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "data.db"
            connection = sqlite3.connect(db_path)
            connection.execute("CREATE TABLE metrics (value INTEGER)")
            connection.executemany(
                "INSERT INTO metrics(value) VALUES (?)",
                [(1,), (2,), (3,)],
            )
            connection.commit()
            connection.close()

            executor = SQLiteMCPExecutor(
                database="analytics",
                db_path=db_path,
                readonly=True,
                max_rows=2,
            )
            result = executor.execute(
                MCPExecutionRequest(
                    database="analytics",
                    sql="SELECT value FROM metrics ORDER BY value",
                )
            )
            self.assertTrue(result.success, result.error)
            self.assertEqual(result.row_count, 2)
            self.assertTrue(result.truncated)

            write_result = executor.execute(
                MCPExecutionRequest(
                    database="analytics",
                    sql="DELETE FROM metrics",
                )
            )
            self.assertFalse(write_result.success)

    def test_mysql_config_reads_prefixed_environment(self) -> None:
        environment = {
            "TEST_MYSQL_HOST": "db.internal",
            "TEST_MYSQL_PORT": "3307",
            "TEST_MYSQL_USER": "readonly",
            "TEST_MYSQL_PASSWORD": "secret",
            "TEST_MYSQL_DATABASE": "analytics",
        }
        with patch.dict(os.environ, environment, clear=False):
            config = MySQLExecutorConfig.from_env("TEST_MYSQL_")
        self.assertEqual(config.host, "db.internal")
        self.assertEqual(config.port, 3307)
        self.assertEqual(config.user, "readonly")
        self.assertEqual(config.database, "analytics")


class PipelineIntegrationTestCase(unittest.TestCase):
    def test_pipeline_repairs_invalid_sql_before_execution(self) -> None:
        class SequenceGenerator:
            def __init__(self) -> None:
                self.calls = 0

            def generate(self, cot_step, sql_dialect="sqlite", correction_context=""):
                self.calls += 1
                sql = (
                    "DELETE FROM interest_info"
                    if self.calls == 1
                    else """SELECT interest_info.interest_rate
FROM trade_summary
JOIN interest_info ON trade_summary.user_id = interest_info.user_id
WHERE trade_summary.total_trade_count > 50000"""
                )
                return SqlGenerationResult(
                    database=cot_step.database,
                    prompt=correction_context,
                    raw_output=sql,
                    sql=sql,
                )

        generator = SequenceGenerator()
        with tempfile.TemporaryDirectory() as temp_dir:
            pipeline = AskDataText2SQLPipeline(
                PipelineConfig(
                    db_path=Path(temp_dir) / "trade.db",
                    max_query_rows=100,
                    max_sql_repair_attempts=2,
                ),
                sql_generator_factory=lambda schema_store: generator,
            )
            result = pipeline.run("查询总交易笔数大于50000的利率是多少")

        self.assertEqual(result.status, AgentRunStatus.COMPLETED, result.error)
        self.assertEqual(generator.calls, 2)
        self.assertEqual(len(result.step_logs[0].sql_attempts), 2)
        self.assertTrue(result.step_logs[0].execution_result["success"])
        self.assertIn("LIMIT 100", result.generated_sql.upper())


if __name__ == "__main__":
    unittest.main()
