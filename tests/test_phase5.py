from __future__ import annotations

import csv
import io
import tempfile
import time
import unittest
import zipfile
from pathlib import Path
from xml.etree import ElementTree

from askdata_memory import SQLiteMemoryStore
from backend.service import AskDataApplicationService, RunAccessError
from reporting import export_csv, export_xlsx


SAMPLE_RESULT = {
    "schema_version": "1.0",
    "run_id": "run_sample",
    "status": "completed",
    "answer": "华东区域销售额最高。",
    "insights": [{"title": "区域领先", "text": "华东贡献最大。"}],
    "table": {
        "columns": ["区域", "销售额", "订单量"],
        "rows": [
            {"区域": "华东", "销售额": 12880.5, "订单量": 32},
            {"区域": "华南", "销售额": 9680.0, "订单量": 25},
        ],
    },
    "charts": [],
    "sql": {
        "text": "SELECT region, SUM(amount) FROM orders GROUP BY region",
        "dialect": "sqlite",
        "duration_ms": 3,
        "validation": {"is_valid": True, "parser": "sqlglot"},
    },
    "scope": {
        "question": "各区域销售额排名是什么？",
        "database": "trade_demo.db",
        "row_count": 2,
        "chart_requested": False,
    },
    "warnings": [],
    "suggested_questions": [],
    "error": None,
}


class ReportExportTestCase(unittest.TestCase):
    def test_csv_is_excel_friendly_and_preserves_unicode(self) -> None:
        content = export_csv(SAMPLE_RESULT)
        self.assertTrue(content.startswith(b"\xef\xbb\xbf"))
        rows = list(csv.reader(io.StringIO(content.decode("utf-8-sig"))))
        self.assertEqual(rows[0], ["区域", "销售额", "订单量"])
        self.assertEqual(rows[1], ["华东", "12880.5", "32"])

    def test_xlsx_is_a_valid_three_sheet_ooxml_package(self) -> None:
        content = export_xlsx(SAMPLE_RESULT)
        with zipfile.ZipFile(io.BytesIO(content)) as workbook:
            self.assertIsNone(workbook.testzip())
            names = set(workbook.namelist())
            self.assertTrue(
                {
                    "xl/workbook.xml",
                    "xl/styles.xml",
                    "xl/worksheets/sheet1.xml",
                    "xl/worksheets/sheet2.xml",
                    "xl/worksheets/sheet3.xml",
                }
                <= names
            )
            for name in names:
                if name.endswith((".xml", ".rels")):
                    ElementTree.fromstring(workbook.read(name))
            workbook_xml = workbook.read("xl/workbook.xml").decode("utf-8")
            self.assertIn("分析概览", workbook_xml)
            self.assertIn("数据明细", workbook_xml)
            self.assertIn("SQL", workbook_xml)


class SavedAnalysisStoreTestCase(unittest.TestCase):
    def test_saved_analyses_are_upserted_and_user_isolated(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = SQLiteMemoryStore(Path(temp_dir) / "memory.db")
            first = store.save_analysis(
                user_id="u1",
                session_id="s1",
                run_id="r1",
                title="第一次保存",
                query="问题",
                result=SAMPLE_RESULT,
            )
            second = store.save_analysis(
                user_id="u1",
                session_id="s1",
                run_id="r1",
                title="更新标题",
                query="问题",
                result=SAMPLE_RESULT,
            )
            self.assertEqual(first["id"], second["id"])
            self.assertEqual(store.list_saved_analyses(user_id="u1")[0]["title"], "更新标题")
            self.assertEqual(store.list_saved_analyses(user_id="u2"), [])
            self.assertIsNone(
                store.get_saved_analysis(user_id="u2", analysis_id=first["id"])
            )
            self.assertTrue(store.delete_saved_analysis(user_id="u1", analysis_id=first["id"]))
            self.assertFalse(store.delete_saved_analysis(user_id="u1", analysis_id=first["id"]))


class ApplicationSavedAnalysisTestCase(unittest.TestCase):
    def test_completed_run_can_be_saved_and_exported_by_owner_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            application = AskDataApplicationService(runtime_dir=temp_dir)
            try:
                record = application.submit_chat(
                    user_id="u1",
                    session_id="s1",
                    query="各区域订单量排名是什么？",
                )
                deadline = time.time() + 5
                while time.time() < deadline:
                    if application.runs.snapshot(record.run_id)["status"] in {
                        "completed",
                        "failed",
                        "cancelled",
                    }:
                        break
                    time.sleep(0.01)
                result = application.get_export_result(user_id="u1", run_id=record.run_id)
                self.assertEqual(result["status"], "completed")
                saved = application.save_analysis(
                    user_id="u1",
                    run_id=record.run_id,
                    title="区域订单分析",
                )
                self.assertEqual(saved["title"], "区域订单分析")
                self.assertEqual(
                    application.get_saved_analysis(
                        user_id="u1", analysis_id=saved["id"]
                    )["result"]["run_id"],
                    record.run_id,
                )
                with self.assertRaises(RunAccessError):
                    application.get_export_result(user_id="u2", run_id=record.run_id)
            finally:
                application.close()


if __name__ == "__main__":
    unittest.main()
