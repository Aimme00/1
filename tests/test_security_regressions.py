from __future__ import annotations

import csv
import io
import tempfile
import unittest
from pathlib import Path

from askdata_pipeline import AgentRunStatus, AskDataText2SQLPipeline, PipelineConfig
from reporting.csv_exporter import export_csv
from sql_validation import SQLValidator


class SchemaAndAnswerRegressionTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.pipeline = AskDataText2SQLPipeline(
            PipelineConfig(db_path=Path(self.temp_dir.name) / "trade.db")
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_core_queries_keep_required_fields_and_complete(self) -> None:
        cases = {
            "最近30天销售额趋势如何？": {"orders": {"order_date", "order_status", "sales_amount"}},
            "生成最近30天销售额趋势图": {"orders": {"order_date", "order_status", "sales_amount"}},
            "销售额最高的前5个产品是哪些？": {"products": {"product_name"}},
            "本月与上月销售额对比如何？": {"orders": {"order_date", "order_status", "sales_amount"}},
        }
        for query, expected in cases.items():
            with self.subTest(query=query):
                retrieval = self.pipeline.schema_retrieval_service.retrieve(
                    query=query,
                    keywords=self.pipeline.keyword_extractor.extract(query),
                )
                for table, required_columns in expected.items():
                    actual = {
                        column.column_name
                        for column in retrieval.schema_graph.columns.get(table, [])
                    }
                    self.assertTrue(required_columns.issubset(actual))
                result = self.pipeline.run(query)
                self.assertEqual(result.status, AgentRunStatus.COMPLETED, result.error)

    def test_refund_rate_uses_orders_instead_of_unrelated_interest_query(self) -> None:
        result = self.pipeline.run("退款率最高的渠道是什么？")
        self.assertEqual(result.status, AgentRunStatus.COMPLETED, result.error)
        self.assertIn("FROM orders", result.generated_sql)
        self.assertIn("refund_rate", result.generated_sql)
        self.assertNotIn("interest_info", result.generated_sql)

    def test_missing_or_write_requests_fail_closed(self) -> None:
        for query in ("查询客户手机号", "删除全部订单"):
            with self.subTest(query=query):
                result = self.pipeline.run(query)
                self.assertEqual(result.status, AgentRunStatus.FAILED)
                self.assertEqual(result.error["code"], "unsupported_query")
                self.assertFalse(result.generated_sql)


class SQLSecurityRegressionTestCase(unittest.TestCase):
    def test_dangerous_and_resource_amplifying_queries_are_rejected(self) -> None:
        validator = SQLValidator()
        dangerous = (
            "SELECT LOAD_FILE('/etc/passwd') FROM orders",
            "SELECT SLEEP(30) FROM orders",
            "SELECT order_id FROM orders FOR UPDATE",
            "SELECT a.order_id FROM orders a CROSS JOIN orders b",
            "WITH RECURSIVE n(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM n) SELECT x FROM n",
            "SELECT hex(zeroblob(100000000)) FROM orders",
        )
        for sql in dangerous:
            with self.subTest(sql=sql):
                result = validator.validate(sql, allowed_tables={"orders"})
                self.assertFalse(result.is_valid, result.to_dict())

    def test_constant_only_fallback_sql_is_not_accepted_for_business_query(self) -> None:
        result = SQLValidator().validate("SELECT 1", allowed_tables={"orders"})
        self.assertFalse(result.is_valid)
        self.assertIn("no_business_table", {issue.code for issue in result.issues})


class CSVSecurityRegressionTestCase(unittest.TestCase):
    def test_formula_like_strings_are_exported_as_text(self) -> None:
        payload = {
            "table": {
                "columns": ["name", "value"],
                "rows": [
                    ["=HYPERLINK(\"https://evil.example\",\"click\")", "+1+1"],
                    ["@SUM(1,1)", "-2+3"],
                    ["normal", -2],
                ],
            }
        }
        decoded = export_csv(payload).decode("utf-8-sig")
        rows = list(csv.reader(io.StringIO(decoded)))
        self.assertTrue(rows[1][0].startswith("'="))
        self.assertTrue(rows[1][1].startswith("'+"))
        self.assertTrue(rows[2][0].startswith("'@"))
        self.assertTrue(rows[2][1].startswith("'-"))
        self.assertEqual(rows[3][1], "-2")


if __name__ == "__main__":
    unittest.main()
