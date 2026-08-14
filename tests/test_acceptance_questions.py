from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from askdata_pipeline import AgentRunStatus, AskDataText2SQLPipeline, PipelineConfig


class AcceptanceQuestionSuiteTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        fixture = Path(__file__).parent / "fixtures" / "acceptance_questions.json"
        cls.questions = json.loads(fixture.read_text(encoding="utf-8"))

    def test_suite_has_exactly_twenty_questions_and_ten_real_model_cases(self) -> None:
        self.assertEqual(len(self.questions), 20)
        self.assertEqual(sum(item["mode"] == "real" for item in self.questions), 10)
        self.assertEqual(sum(item["mode"] == "deterministic" for item in self.questions), 10)
        self.assertEqual(len({item["id"] for item in self.questions}), 20)

    def test_ten_deterministic_questions_run_through_complete_pipeline(self) -> None:
        cases = [item for item in self.questions if item["mode"] == "deterministic"]
        with tempfile.TemporaryDirectory() as temp_dir:
            pipeline = AskDataText2SQLPipeline(
                PipelineConfig(
                    db_path=Path(temp_dir) / "acceptance.db",
                    max_sql_repair_attempts=0,
                    max_execution_repair_attempts=0,
                    require_sqlglot=False,
                )
            )
            for case in cases:
                with self.subTest(case=case["id"], question=case["question"]):
                    result = pipeline.run(case["question"])
                    expected = AgentRunStatus(case["expect"])
                    self.assertEqual(result.status, expected, result.error)
                    if expected == AgentRunStatus.COMPLETED:
                        self.assertTrue(result.query_result.get("success"), result.error)
                        self.assertTrue(result.generated_sql.strip())
                        self.assertNotIn("DELETE", result.generated_sql.upper())
                    else:
                        self.assertEqual(result.error.get("code"), case["error_code"])


if __name__ == "__main__":
    unittest.main()
