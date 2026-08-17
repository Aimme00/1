from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
import os
import re
import sqlite3
from collections import defaultdict
from datetime import date, timedelta
from unittest.mock import patch

from askdata_pipeline import AgentRunStatus, AskDataText2SQLPipeline, PipelineConfig
from askdata_pipeline.verified_demo_questions import VERIFIED_DEMO_QUESTIONS
from model_call_budget import model_call_budget
from sql_validation import SQLValidator, SQLValidatorConfig


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class MvpProductSurfaceTestCase(unittest.TestCase):
    def test_visible_examples_are_exactly_the_verified_catalog(self) -> None:
        index = (PROJECT_ROOT / "web" / "index.html").read_text(encoding="utf-8")
        visible_questions = re.findall(r'data-query="([^"]+)"', index)
        self.assertEqual(
            visible_questions,
            [item.question for item in VERIFIED_DEMO_QUESTIONS],
        )

    def test_home_restores_the_full_workbench_around_the_verified_demo(self) -> None:
        index = (PROJECT_ROOT / "web" / "index.html").read_text(encoding="utf-8")
        script = (PROJECT_ROOT / "web" / "app.js").read_text(encoding="utf-8")
        combined = index + script

        for text in (
            "问数工作台",
            "描述性数据分析助手",
            "新建分析",
            "我的仪表盘",
            "最近会话",
            "已保存分析",
            "保存分析",
            "添加到仪表盘",
            "下载 CSV",
            "下载 Excel",
            "下载图表 PNG",
            "查看生成的查询",
            "安全查询",
            "当前版本不提供异常诊断、原因归因、预测或策略建议。",
        ):
            self.assertIn(text, combined)

    def test_readme_states_accuracy_and_product_boundaries(self) -> None:
        readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("个人 AI 产品与 Vibe Coding 原型验证", readme)
        self.assertIn("不是生产级数据分析平台", readme)
        self.assertIn("当前没有稳定、独立的结构化意图解析层", readme)
        self.assertIn("当前没有 SQL 语义一致性校验", readme)
        self.assertIn("真实模型全量回归尚未完成", readme)
        self.assertIn("SQL 可执行", readme)


class MvpCoreOfflineSmokeTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        cls.db_path = Path(cls.temp_dir.name) / "mvp.db"
        cls.pipeline = AskDataText2SQLPipeline(
            PipelineConfig(
                db_path=cls.db_path,
                max_sql_repair_attempts=0,
                max_execution_repair_attempts=0,
                require_sqlglot=False,
            )
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temp_dir.cleanup()

    def assert_query_success(self, question: str, columns: list[str], rows: int) -> dict:
        result = self.pipeline.run(question)
        self.assertEqual(result.status, AgentRunStatus.COMPLETED, result.error)
        self.assertTrue(result.query_result.get("success"), result.error)
        self.assertTrue(result.generated_sql.strip())
        self.assertEqual(result.table["columns"], columns)
        self.assertEqual(result.query_result["row_count"], rows)
        self.assertNotIn("DELETE", result.generated_sql.upper())
        return result.to_api_response()

    def test_recent_sales_trend_is_executable_and_has_expected_shape(self) -> None:
        response = self.assert_query_success(
            "最近30天销售额趋势如何？", ["order_date", "sales_amount"], 30
        )
        values = [row["sales_amount"] for row in response["table"]["rows"]]
        self.assertTrue(all(value >= 0 for value in values))

    def test_region_order_ranking_is_sorted(self) -> None:
        response = self.assert_query_success(
            "各区域已完成订单量排名是什么？", ["region", "order_count"], 6
        )
        values = [row["order_count"] for row in response["table"]["rows"]]
        self.assertEqual(values, sorted(values, reverse=True))

    def test_top_five_products_are_sorted(self) -> None:
        response = self.assert_query_success(
            "已完成订单中，销售额最高的前5个产品是哪些？", ["product_name", "sales_amount"], 5
        )
        values = [row["sales_amount"] for row in response["table"]["rows"]]
        self.assertEqual(values, sorted(values, reverse=True))

    def test_month_comparison_is_executable(self) -> None:
        self.assert_query_success(
            "本月与上月已完成订单销售额相比变化多少？",
            ["sales_month", "sales_amount"],
            2,
        )

    def test_verified_trend_chart_matches_the_returned_table_exactly(self) -> None:
        result = self.pipeline.run(
            VERIFIED_DEMO_QUESTIONS[0].question,
            generate_chart=True,
        )
        self.assertEqual(result.status, AgentRunStatus.COMPLETED, result.error)
        self.assertEqual(len(result.chart_configs), 1)
        chart = result.chart_configs[0]
        self.assertEqual(chart["type"], "line")
        rows = result.table["rows"]
        self.assertEqual(
            chart["option"]["xAxis"]["data"],
            [row["order_date"] for row in rows],
        )
        self.assertEqual(
            chart["option"]["series"][0]["data"],
            [row["sales_amount"] for row in rows],
        )

    def test_all_verified_postgres_sql_is_parseable_and_read_only(self) -> None:
        validator = SQLValidator(
            SQLValidatorConfig(dialect="postgres", require_sqlglot=True)
        )
        allowed_columns = {
            "orders": {
                "order_id", "customer_id", "order_date", "sales_amount", "order_status"
            },
            "customers": {"customer_id", "region"},
            "products": {"product_id", "product_name"},
            "order_items": {"order_id", "product_id", "line_amount"},
        }
        for item in VERIFIED_DEMO_QUESTIONS:
            with self.subTest(question=item.question):
                sql = item.sql_for("postgres")
                result = validator.validate(
                    sql,
                    allowed_tables=allowed_columns,
                    allowed_columns=allowed_columns,
                )
                self.assertTrue(result.is_valid, result.to_dict())
                self.assertEqual(result.parser, "sqlglot")
                self.assertNotIn("STRFTIME", sql.upper())
                self.assertNotIn("DATE('NOW'", sql.upper())

    def test_every_visible_demo_question_uses_no_model_and_matches_oracle(self) -> None:
        with patch.dict(
            os.environ,
            {
                "ASKDATA_LLM_PROVIDER": "deepseek",
                "DEEPSEEK_API_KEY": "must-not-be-called",
            },
            clear=False,
        ), model_call_budget(0) as budget:
            for item in VERIFIED_DEMO_QUESTIONS:
                with self.subTest(question=item.question):
                    result = self.pipeline.run(item.question)
                    self.assertEqual(result.status, AgentRunStatus.COMPLETED, result.error)
                    self.assertEqual(tuple(result.table["columns"]), item.expected_columns)
                    self.assertEqual(result.query_result["row_count"], item.expected_rows)
                    self.assertTrue(result.scope.get("verified_demo"))
                    self.assertTrue(result.validation_result.get("is_valid"))
            self.assertEqual(budget.calls, 0)

    def test_verified_answers_match_independent_raw_row_calculation(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            orders = [dict(row) for row in conn.execute("SELECT * FROM orders")]
            customers = {
                row["customer_id"]: row["region"]
                for row in conn.execute("SELECT customer_id, region FROM customers")
            }
            products = {
                row["product_id"]: row["product_name"]
                for row in conn.execute("SELECT product_id, product_name FROM products")
            }
            items = [dict(row) for row in conn.execute("SELECT * FROM order_items")]

        completed = [row for row in orders if row["order_status"] == "completed"]
        order_by_id = {row["order_id"]: row for row in completed}

        trend = defaultdict(float)
        latest_order_date = max(date.fromisoformat(row["order_date"]) for row in orders)
        cutoff = latest_order_date - timedelta(days=29)
        for row in completed:
            if date.fromisoformat(row["order_date"]) >= cutoff:
                trend[row["order_date"]] += row["sales_amount"]

        regions = defaultdict(int)
        for row in completed:
            regions[customers[row["customer_id"]]] += 1

        product_sales = defaultdict(float)
        for item in items:
            if item["order_id"] in order_by_id:
                product_sales[products[item["product_id"]]] += item["line_amount"]

        monthly = defaultdict(float)
        current_month = latest_order_date.strftime("%Y-%m")
        previous_month_end = latest_order_date.replace(day=1) - timedelta(days=1)
        previous_month = previous_month_end.strftime("%Y-%m")
        for row in completed:
            month = row["order_date"][:7]
            if month in {previous_month, current_month}:
                monthly[month] += row["sales_amount"]

        expected = {
            VERIFIED_DEMO_QUESTIONS[0].question: [
                {"order_date": key, "sales_amount": round(value, 2)}
                for key, value in sorted(trend.items())
            ],
            VERIFIED_DEMO_QUESTIONS[1].question: [
                {"region": key, "order_count": value}
                for key, value in sorted(regions.items(), key=lambda item: (-item[1], item[0]))
            ],
            VERIFIED_DEMO_QUESTIONS[2].question: [
                {"product_name": key, "sales_amount": round(value, 2)}
                for key, value in sorted(product_sales.items(), key=lambda item: (-item[1], item[0]))[:5]
            ],
            VERIFIED_DEMO_QUESTIONS[3].question: [
                {"sales_month": key, "sales_amount": round(value, 2)}
                for key, value in sorted(monthly.items())
            ],
        }
        for question, oracle_rows in expected.items():
            with self.subTest(question=question):
                result = self.pipeline.run(question)
                self.assertEqual(result.table["rows"], oracle_rows)


if __name__ == "__main__":
    unittest.main()
