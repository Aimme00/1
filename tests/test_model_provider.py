from __future__ import annotations

import json
import os
import unittest
from unittest.mock import patch

from cot_planning import ThinkingModelClient, ThinkingModelConfig
from model_provider import resolve_model_settings
from sql_generation import CoderModelClient, CoderModelConfig


class _FakeResponse:
    def __init__(self, content: str):
        self.body = json.dumps({"choices": [{"message": {"content": content}}]}).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self):
        return self.body


class DeepSeekProviderTestCase(unittest.TestCase):
    def setUp(self):
        self.requests = []

    def fake_urlopen(self, request, timeout):
        payload = json.loads(request.data.decode("utf-8"))
        self.requests.append(
            {
                "url": request.full_url,
                "authorization": request.get_header("Authorization"),
                "payload": payload,
                "timeout": timeout,
            }
        )
        content = "规划结果" if len(self.requests) == 1 else "SELECT 1;"
        return _FakeResponse(content)

    def test_deepseek_is_selected_and_both_agent_nodes_use_openai_protocol(self):
        endpoint = "https://local.deepseek.test/chat/completions"
        env = {
            "ASKDATA_LLM_PROVIDER": "deepseek",
            "DEEPSEEK_API_KEY": "local-test-key",
            "DEEPSEEK_BASE_URL": endpoint,
            "DEEPSEEK_COT_MODEL": "deepseek-v4-flash",
            "DEEPSEEK_CODER_MODEL": "deepseek-v4-flash",
        }
        with patch.dict(os.environ, env, clear=True), patch(
            "urllib.request.urlopen", side_effect=self.fake_urlopen
        ):
            thinking = ThinkingModelClient(ThinkingModelConfig(use_mock_when_no_api_key=False))
            coder = CoderModelClient(CoderModelConfig(use_mock_when_no_api_key=False))
            self.assertEqual(thinking.generate("plan"), "规划结果")
            self.assertEqual(coder.generate_sql("sql"), "SELECT 1;")

        self.assertEqual(len(self.requests), 2)
        for request in self.requests:
            self.assertEqual(request["url"], endpoint)
            self.assertEqual(request["authorization"], "Bearer local-test-key")
            self.assertEqual(request["payload"]["model"], "deepseek-v4-flash")
            self.assertFalse(request["payload"]["stream"])

    def test_mock_remains_available_without_any_key(self):
        with patch.dict(os.environ, {}, clear=True):
            settings = resolve_model_settings(role="cot")
            self.assertEqual(settings.provider, "dashscope")
            self.assertEqual(settings.api_key, "")
            client = ThinkingModelClient(ThinkingModelConfig(use_mock_when_no_api_key=True))
            self.assertIn("步骤1", client.generate("用户问题：查询利率"))


if __name__ == "__main__":
    unittest.main()
