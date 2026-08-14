from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from cot_planning.thinking_client import ThinkingModelClient, ThinkingModelConfig
from model_call_budget import ModelCallLimitExceeded, model_call_budget
from sql_generation.coder_client import CoderModelClient, CoderModelConfig
from scripts.run_acceptance_suite import _run_cases


class _FakeResponse:
    def __init__(self, content: str):
        self.body = json.dumps(
            {"choices": [{"message": {"content": content}}]}
        ).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self):
        return self.body


class ModelCallBudgetTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.thinking = ThinkingModelClient(
            ThinkingModelConfig(
                provider="deepseek",
                api_key="test-key",
                base_url="https://model.invalid/chat",
                model="thinking-test",
            )
        )
        self.coder = CoderModelClient(
            CoderModelConfig(
                provider="deepseek",
                api_key="test-key",
                base_url="https://model.invalid/chat",
                model="coder-test",
            )
        )

    @patch("urllib.request.urlopen")
    def test_budget_is_shared_by_both_model_clients(self, urlopen) -> None:
        urlopen.side_effect = [
            _FakeResponse("plan"),
            _FakeResponse("SELECT 1"),
        ]
        with model_call_budget(2) as budget:
            self.assertEqual(self.thinking.generate("question"), "plan")
            self.assertEqual(self.coder.generate_sql("prompt"), "SELECT 1")
            with self.assertRaises(ModelCallLimitExceeded):
                self.thinking.generate("blocked")

        self.assertEqual(budget.calls, 2)
        self.assertEqual(budget.remaining, 0)
        self.assertEqual([record.role for record in budget.records], ["cot", "coder"])
        self.assertEqual(urlopen.call_count, 2)

    @patch("urllib.request.urlopen")
    def test_zero_budget_blocks_before_network(self, urlopen) -> None:
        with model_call_budget(0) as budget:
            with self.assertRaises(ModelCallLimitExceeded):
                self.coder.generate_sql("prompt")
        self.assertEqual(budget.calls, 0)
        urlopen.assert_not_called()

    def test_acceptance_runner_stops_after_budget_error(self) -> None:
        class _BudgetExhaustedPipeline:
            calls = 0

            def run(self, question, generate_chart=None):
                self.calls += 1
                return SimpleNamespace(
                    error={
                        "code": "pipeline_error",
                        "message": "真实模型调用预算已用完：上限 20 次；第 21 次请求未发送",
                    }
                )

        pipeline = _BudgetExhaustedPipeline()
        cases = [
            {"id": 1, "question": "first", "chart": False},
            {"id": 2, "question": "second", "chart": False},
        ]
        with self.assertRaises(ModelCallLimitExceeded):
            _run_cases(pipeline, cases)
        self.assertEqual(pipeline.calls, 1)


if __name__ == "__main__":
    unittest.main()
