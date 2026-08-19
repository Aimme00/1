from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from askdata_pipeline import AgentRunStatus, AskDataText2SQLPipeline, PipelineConfig


class SalesDemoDatabaseTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        cls.db_path = Path(cls.temp_dir.name) / "sales_demo.db"
        cls.pipeline = AskDataText2SQLPipeline(PipelineConfig(db_path=cls.db_path))

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temp_dir.cleanup()

    def test_demo_database_contains_sales_domain(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
            order_count = conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
            item_count = conn.execute("SELECT COUNT(*) FROM order_items").fetchone()[0]
        self.assertTrue({"customers", "products", "orders", "order_items"} <= tables)
        self.assertEqual(order_count, 360)
        self.assertEqual(item_count, 360)

    def test_recent_30_day_sales_trend(self) -> None:
        result = self.pipeline.run("最近30天销售额趋势如何？")
        self.assertEqual(result.status, AgentRunStatus.COMPLETED)
        self.assertEqual(result.query_result["row_count"], 30)
        self.assertEqual(result.table["columns"], ["order_date", "sales_amount"])
        self.assertFalse(result.scope["chart_requested"])
        self.assertEqual(result.chart_configs, [])

    def test_region_order_ranking(self) -> None:
        result = self.pipeline.run("各区域订单量排名是什么？")
        self.assertEqual(result.status, AgentRunStatus.COMPLETED)
        self.assertEqual(result.query_result["row_count"], 6)
        self.assertEqual(result.table["columns"], ["region", "order_count"])
        counts = [row["order_count"] for row in result.table["rows"]]
        self.assertEqual(counts, sorted(counts, reverse=True))

    def test_top_five_products_by_sales(self) -> None:
        result = self.pipeline.run("销售额最高的前5个产品是哪些？")
        self.assertEqual(result.status, AgentRunStatus.COMPLETED)
        self.assertEqual(result.query_result["row_count"], 5)
        self.assertEqual(result.table["columns"], ["product_name", "sales_amount"])
        amounts = [row["sales_amount"] for row in result.table["rows"]]
        self.assertEqual(amounts, sorted(amounts, reverse=True))

    def test_bottom_five_products_by_sales(self) -> None:
        result = self.pipeline.run("销售额最低的5个产品是哪些？")
        self.assertEqual(result.status, AgentRunStatus.COMPLETED)
        self.assertEqual(result.query_result["row_count"], 5)
        self.assertEqual(result.table["columns"], ["product_name", "sales_amount"])
        amounts = [row["sales_amount"] for row in result.table["rows"]]
        self.assertEqual(amounts, sorted(amounts))

    def test_product_sales_ranking_preserves_requested_limit(self) -> None:
        highest = self.pipeline.run("已完成订单中销售额最高的前3个商品是哪些？")
        lowest = self.pipeline.run("已完成订单中销售额最低的3个商品是哪些？")

        self.assertEqual(highest.status, AgentRunStatus.COMPLETED)
        self.assertEqual(lowest.status, AgentRunStatus.COMPLETED)
        self.assertEqual(highest.query_result["row_count"], 3)
        self.assertEqual(lowest.query_result["row_count"], 3)
        self.assertEqual(
            [row["sales_amount"] for row in highest.table["rows"]],
            sorted(
                [row["sales_amount"] for row in highest.table["rows"]],
                reverse=True,
            ),
        )
        self.assertEqual(
            [row["sales_amount"] for row in lowest.table["rows"]],
            sorted([row["sales_amount"] for row in lowest.table["rows"]]),
        )

    def test_current_and_previous_month_comparison(self) -> None:
        result = self.pipeline.run("本月与上月销售额相比变化多少？")
        self.assertEqual(result.status, AgentRunStatus.COMPLETED)
        self.assertEqual(result.query_result["row_count"], 2)
        self.assertEqual(result.table["columns"], ["sales_month", "sales_amount"])

    def test_anomaly_query_surfaces_seeded_spike(self) -> None:
        result = self.pipeline.run("找出销售额异常的日期并说明原因")
        self.assertEqual(result.status, AgentRunStatus.COMPLETED)
        first = result.table["rows"][0]
        self.assertGreater(first["anomaly_ratio"], 3)
        self.assertGreater(first["sales_amount"], result.table["rows"][1]["sales_amount"])
        self.assertEqual(first["total_quantity"], 40)
        self.assertIn("候选原因", result.final_answer)

    def test_explicit_chart_request_generates_line_chart(self) -> None:
        result = self.pipeline.run("请生成最近30天销售额折线图")
        self.assertEqual(result.status, AgentRunStatus.COMPLETED)
        self.assertTrue(result.scope["chart_requested"])
        self.assertEqual(result.chart_configs[0]["type"], "line")


if __name__ == "__main__":
    unittest.main()
