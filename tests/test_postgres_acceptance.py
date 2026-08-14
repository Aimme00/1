from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from askdata_pipeline import AgentRunStatus, AskDataText2SQLPipeline, PipelineConfig
from mcp_router import MCPExecutionResult
from model_call_budget import model_call_budget


class _RecordingPostgresExecutor:
    def __init__(self) -> None:
        self.sql: list[str] = []

    def execute(self, request):
        self.sql.append(request.sql)
        return MCPExecutionResult(
            database=request.database,
            sql=request.sql,
            success=True,
            columns=["value"],
            rows=[{"value": 1}],
            row_count=1,
        )


class PostgresAcceptanceTestCase(unittest.TestCase):
    def test_all_ten_business_intents_generate_valid_postgres_sql_without_api(self):
        fixture = Path(__file__).parent / "fixtures" / "acceptance_questions.json"
        cases = [
            item
            for item in json.loads(fixture.read_text(encoding="utf-8"))
            if item["mode"] == "real"
        ]
        self.assertEqual(len(cases), 10)

        with patch.dict(os.environ, {}, clear=True), tempfile.TemporaryDirectory() as temp_dir:
            sqlite_pipeline = AskDataText2SQLPipeline(
                PipelineConfig(db_path=Path(temp_dir) / "schema.db")
            )
            executor = _RecordingPostgresExecutor()
            pipeline = AskDataText2SQLPipeline(
                PipelineConfig(
                    database_name="trade_db",
                    db_path=Path(temp_dir) / "unused.db",
                    database_type="postgres",
                    sql_dialect="postgres",
                    bootstrap_demo_database=False,
                    max_sql_repair_attempts=0,
                    max_execution_repair_attempts=0,
                    require_sqlglot=True,
                ),
                schema_retrieval_service=sqlite_pipeline.schema_retrieval_service,
                query_executor=executor,
                business_meta=sqlite_pipeline.business_meta,
            )
            with model_call_budget(0) as budget:
                for case in cases:
                    with self.subTest(case=case["id"], question=case["question"]):
                        result = pipeline.run(
                            case["question"],
                            generate_chart=case.get("chart"),
                        )
                        self.assertEqual(result.status, AgentRunStatus.COMPLETED, result.error)
                        sql = result.generated_sql.lower()
                        self.assertNotIn("strftime(", sql)
                        self.assertNotIn("date('now'", sql)
                        self.assertNotIn('date("now"', sql)
                        expected_columns = [
                            str(column).lower()
                            for column in case.get("columns_any") or []
                        ]
                        self.assertTrue(
                            not expected_columns
                            or any(column in sql for column in expected_columns),
                            f"SQL 未覆盖任一预期输出字段 {expected_columns}: {sql}",
                        )
                        for column in case.get("columns_all") or []:
                            self.assertIn(
                                str(column).lower(),
                                sql,
                                f"SQL 缺少必须输出字段 {column}: {sql}",
                            )
                        if case["id"] == 8:
                            self.assertNotIn("interest_info", sql)
                            self.assertNotIn("total_trade_count", sql)
            self.assertEqual(budget.calls, 0)
            self.assertEqual(len(executor.sql), 10)


if __name__ == "__main__":
    unittest.main()
