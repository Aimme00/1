from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from askdata_memory import SQLiteMemoryStore


SAMPLE_RESULT = {
    "schema_version": "1.0",
    "run_id": "run_dashboard",
    "status": "completed",
    "answer": "最近 30 天销售额整体上升。",
    "insights": [{"title": "趋势", "text": "销售额较期初增长。"}],
    "table": {"columns": ["日期", "销售额"], "rows": [["2026-08-01", 100]]},
    "charts": [],
    "sql": {"text": "SELECT 1", "dialect": "sqlite", "duration_ms": 1},
    "scope": {"database": "trade_db", "row_count": 1},
    "warnings": [],
    "suggested_questions": [],
    "error": None,
}


class DashboardStoreTestCase(unittest.TestCase):
    def test_dashboard_cards_are_idempotent_and_user_isolated(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = SQLiteMemoryStore(Path(temp_dir) / "memory.db")
            analysis = store.save_analysis(
                user_id="u1",
                session_id="s1",
                run_id="r1",
                title="销售趋势",
                query="最近30天销售额趋势如何？",
                result=SAMPLE_RESULT,
            )
            other_analysis = store.save_analysis(
                user_id="u2",
                session_id="s2",
                run_id="r2",
                title="其他用户分析",
                query="问题",
                result=SAMPLE_RESULT,
            )
            dashboard = store.create_dashboard(
                user_id="u1",
                name="经营驾驶舱",
                description="核心经营指标",
            )

            first = store.add_dashboard_card(
                user_id="u1",
                dashboard_id=dashboard["id"],
                analysis_id=analysis["id"],
            )
            duplicate = store.add_dashboard_card(
                user_id="u1",
                dashboard_id=dashboard["id"],
                analysis_id=analysis["id"],
            )

            self.assertIsNotNone(first)
            self.assertEqual(first["id"], duplicate["id"])
            self.assertIsNone(
                store.add_dashboard_card(
                    user_id="u1",
                    dashboard_id=dashboard["id"],
                    analysis_id=other_analysis["id"],
                )
            )
            self.assertIsNone(
                store.get_dashboard(user_id="u2", dashboard_id=dashboard["id"])
            )
            loaded = store.get_dashboard(
                user_id="u1",
                dashboard_id=dashboard["id"],
            )
            self.assertEqual(loaded["card_count"], 1)
            self.assertEqual(loaded["cards"][0]["result"]["answer"], SAMPLE_RESULT["answer"])

    def test_saved_analysis_delete_removes_dashboard_card(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = SQLiteMemoryStore(Path(temp_dir) / "memory.db")
            analysis = store.save_analysis(
                user_id="u1",
                session_id="s1",
                run_id="r1",
                title="销售趋势",
                query="问题",
                result=SAMPLE_RESULT,
            )
            dashboard = store.create_dashboard(user_id="u1", name="我的仪表盘")
            store.add_dashboard_card(
                user_id="u1",
                dashboard_id=dashboard["id"],
                analysis_id=analysis["id"],
            )

            self.assertTrue(
                store.delete_saved_analysis(user_id="u1", analysis_id=analysis["id"])
            )
            loaded = store.get_dashboard(user_id="u1", dashboard_id=dashboard["id"])
            self.assertEqual(loaded["cards"], [])

    def test_dashboard_delete_is_owner_scoped(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = SQLiteMemoryStore(Path(temp_dir) / "memory.db")
            dashboard = store.create_dashboard(user_id="u1", name="我的仪表盘")
            self.assertFalse(
                store.delete_dashboard(user_id="u2", dashboard_id=dashboard["id"])
            )
            self.assertTrue(
                store.delete_dashboard(user_id="u1", dashboard_id=dashboard["id"])
            )


class DashboardFrontendContractTestCase(unittest.TestCase):
    def test_frontend_exposes_dashboard_workflow(self) -> None:
        base_dir = Path(__file__).resolve().parents[1]
        html = (base_dir / "web" / "index.html").read_text(encoding="utf-8")
        javascript = (base_dir / "web" / "app.js").read_text(encoding="utf-8")
        self.assertIn('id="dashboardButton"', html)
        self.assertIn('id="addDashboardButton"', html)
        self.assertIn("/api/dashboards", javascript)
        self.assertIn("addResultToDashboard", javascript)
        self.assertTrue((base_dir / "web" / "dashboard.css").is_file())


if __name__ == "__main__":
    unittest.main()
