from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

from askdata_pipeline import AgentRunStatus, AgentState, AskDataText2SQLPipeline, PipelineConfig
from chart_generation import EChartsRecommender
from data_analysis import DeterministicDataAnalyzer
from response_generation import GroundedResponseGenerator
from result_quality import ResultQualityValidator


class ResultQualityTestCase(unittest.TestCase):
    def test_empty_successful_result_is_usable_but_marked_empty(self) -> None:
        report = ResultQualityValidator().validate(
            {
                "success": True,
                "columns": ["sales"],
                "rows": [],
                "row_count": 0,
                "truncated": False,
            }
        )
        self.assertTrue(report.usable)
        self.assertTrue(report.empty)
        self.assertEqual(report.issues[0].code, "no_data")

    def test_truncated_result_has_explicit_warning(self) -> None:
        report = ResultQualityValidator().validate(
            {
                "success": True,
                "columns": ["sales"],
                "rows": [{"sales": 1}],
                "row_count": 1,
                "truncated": True,
            }
        )
        self.assertTrue(report.usable)
        self.assertTrue(report.truncated)
        self.assertIn("返回上限", report.warnings[0])

    def test_malformed_rows_prevent_unsupported_conclusion(self) -> None:
        report = ResultQualityValidator().validate(
            {
                "success": True,
                "columns": ["sales"],
                "rows": [123],
                "row_count": 1,
            }
        )
        self.assertFalse(report.usable)


class DataAnalysisTestCase(unittest.TestCase):
    def test_numeric_summary_is_deterministic(self) -> None:
        result = {
            "columns": ["interest_rate"],
            "rows": [
                {"interest_rate": 4.58},
                {"interest_rate": 4.95},
                {"interest_rate": 4.2},
                {"interest_rate": 5.15},
                {"interest_rate": 5.05},
            ],
        }
        analysis = DeterministicDataAnalyzer().analyze("查询利率", result)
        summary = analysis.insights[0]
        self.assertEqual(summary.type, "summary")
        self.assertAlmostEqual(summary.evidence["average"], 4.786)
        self.assertIn("4.79", summary.text)

    def test_time_series_generates_trend_insight(self) -> None:
        result = {
            "columns": ["sales_date", "sales_amount"],
            "rows": [
                {"sales_date": "2026-01-01", "sales_amount": 100},
                {"sales_date": "2026-01-02", "sales_amount": 120},
            ],
        }
        analysis = DeterministicDataAnalyzer().analyze("销售额趋势", result)
        trend = next(insight for insight in analysis.insights if insight.type == "trend")
        self.assertIn("上升", trend.text)
        self.assertAlmostEqual(trend.evidence["change_rate"], 20.0)

    def test_identifier_is_not_selected_as_primary_metric(self) -> None:
        result = {
            "columns": ["user_id", "sales_amount"],
            "rows": [
                {"user_id": 1001, "sales_amount": 10},
                {"user_id": 1002, "sales_amount": 20},
            ],
        }
        analysis = DeterministicDataAnalyzer().analyze("销售额", result)
        self.assertEqual(analysis.primary_metric, "sales_amount")


class ChartRecommendationTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.analyzer = DeterministicDataAnalyzer()
        self.recommender = EChartsRecommender()

    def test_time_series_recommends_sorted_line_chart(self) -> None:
        result = {
            "columns": ["sales_date", "sales_amount"],
            "rows": [
                {"sales_date": "2026-01-02", "sales_amount": 120},
                {"sales_date": "2026-01-01", "sales_amount": 100},
            ],
        }
        analysis = self.analyzer.analyze("销售额趋势", result)
        charts = self.recommender.recommend("销售额趋势", result, analysis)
        self.assertEqual(charts[0].type, "line")
        self.assertEqual(
            charts[0].option["xAxis"]["data"],
            ["2026-01-01", "2026-01-02"],
        )

    def test_category_comparison_recommends_bar_chart(self) -> None:
        result = {
            "columns": ["channel", "sales_amount"],
            "rows": [
                {"channel": "线上", "sales_amount": 120},
                {"channel": "门店", "sales_amount": 80},
            ],
        }
        analysis = self.analyzer.analyze("各渠道销售额", result)
        charts = self.recommender.recommend("各渠道销售额", result, analysis)
        self.assertEqual(charts[0].type, "bar")
        self.assertNotIn("formatter", json.dumps(charts[0].option, ensure_ascii=False))

    def test_share_question_recommends_pie_chart(self) -> None:
        result = {
            "columns": ["channel", "sales_amount"],
            "rows": [
                {"channel": "线上", "sales_amount": 120},
                {"channel": "门店", "sales_amount": 80},
            ],
        }
        analysis = self.analyzer.analyze("各渠道销售额占比", result)
        charts = self.recommender.recommend("各渠道销售额占比", result, analysis)
        self.assertEqual(charts[0].type, "pie")


class ResponseAndContractTestCase(unittest.TestCase):
    def test_empty_result_response_does_not_invent_numbers(self) -> None:
        result = {
            "success": True,
            "columns": ["sales"],
            "rows": [],
            "row_count": 0,
        }
        quality = ResultQualityValidator().validate(result)
        analysis = DeterministicDataAnalyzer().analyze("销售额", result)
        response = GroundedResponseGenerator().generate(
            query="销售额",
            result=result,
            quality=quality,
            analysis=analysis,
            database="analytics",
        )
        self.assertIn("没有数据", response.answer)

    def test_api_response_is_json_serializable(self) -> None:
        state = AgentState(
            query="测试",
            status=AgentRunStatus.COMPLETED,
            final_answer="完成",
            table={
                "columns": ["day", "amount"],
                "rows": [{"day": date(2026, 1, 1), "amount": Decimal("12.30")}],
            },
        )
        encoded = json.dumps(state.to_api_response(), ensure_ascii=False)
        self.assertIn("2026-01-01", encoded)
        self.assertIn("12.3", encoded)

    def test_pipeline_returns_complete_product_response(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            pipeline = AskDataText2SQLPipeline(
                PipelineConfig(db_path=Path(temp_dir) / "trade.db")
            )
            result = pipeline.run("查询总交易笔数大于50000的利率是多少")
        response = result.to_api_response()
        self.assertEqual(response["schema_version"], "1.0")
        self.assertEqual(response["status"], "completed")
        self.assertIn("平均值", response["answer"])
        self.assertEqual(response["table"]["total_rows"], 5)
        self.assertEqual(response["table"]["column_meta"][0]["label"], "利率")
        self.assertTrue(response["sql"]["text"].startswith("SELECT"))
        self.assertEqual(len(response["insights"]), 1)


if __name__ == "__main__":
    unittest.main()
