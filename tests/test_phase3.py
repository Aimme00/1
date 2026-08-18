from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from askdata_memory import SQLiteMemoryStore
from askdata_memory.objects import MessageRole
from askdata_pipeline import AgentRunStatus, AskDataText2SQLPipeline, PipelineConfig
from backend.run_manager import RunManager
from backend.service import AskDataApplicationService


class RunManagerTestCase(unittest.TestCase):
    def test_run_events_can_be_replayed_and_result_is_retained(self) -> None:
        manager = RunManager(max_workers=1)

        def handler(record, emit, should_cancel):
            emit("schema", "running", "retrieving")
            emit("schema", "completed", "done", {"tables": 2})
            return {"run_id": record.run_id, "status": "completed", "answer": "ok"}

        record = manager.submit(user_id="u1", session_id="s1", query="q", handler=handler)
        snapshot = self._wait_terminal(manager, record.run_id)
        events, terminal = manager.events_after(record.run_id, after=0, timeout=0)
        self.assertEqual(snapshot["status"], "completed")
        self.assertEqual(snapshot["result"]["answer"], "ok")
        self.assertTrue(terminal)
        self.assertEqual([item["sequence"] for item in events], list(range(1, len(events) + 1)))
        self.assertIn("schema", [item["node"] for item in events])
        manager.shutdown()

    def test_cancel_request_is_cooperative(self) -> None:
        manager = RunManager(max_workers=1)

        def handler(record, emit, should_cancel):
            while not should_cancel():
                time.sleep(0.005)
            return {"run_id": record.run_id, "status": "cancelled"}

        record = manager.submit(user_id="u1", session_id="s1", query="q", handler=handler)
        manager.cancel(record.run_id)
        snapshot = self._wait_terminal(manager, record.run_id)
        self.assertEqual(snapshot["status"], "cancelled")
        manager.shutdown()

    @staticmethod
    def _wait_terminal(manager: RunManager, run_id: str):
        deadline = time.time() + 3
        while time.time() < deadline:
            snapshot = manager.snapshot(run_id)
            if snapshot["status"] in {"completed", "failed", "cancelled"}:
                return snapshot
            time.sleep(0.01)
        raise AssertionError("run did not finish")


class PipelineEventsTestCase(unittest.TestCase):
    def test_chart_generation_requires_explicit_user_intent(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            pipeline = AskDataText2SQLPipeline(
                PipelineConfig(db_path=Path(temp_dir) / "trade.db")
            )
            self.assertFalse(pipeline.should_generate_chart("最近30天销售额趋势如何"))
            self.assertTrue(pipeline.should_generate_chart("生成最近30天销售额折线图"))
            self.assertTrue(pipeline.should_generate_chart("请生成销售额对比图表"))
            self.assertTrue(pipeline.should_generate_chart("销售额趋势", explicit_choice=True))
            self.assertFalse(pipeline.should_generate_chart("生成销售额图表", explicit_choice=False))

    def test_pipeline_emits_product_stage_events(self) -> None:
        events = []
        with tempfile.TemporaryDirectory() as temp_dir:
            pipeline = AskDataText2SQLPipeline(
                PipelineConfig(db_path=Path(temp_dir) / "trade.db")
            )
            result = pipeline.run(
                "查询总交易笔数大于50000的利率是多少",
                event_callback=lambda node, status, message, data: events.append((node, status)),
            )
        self.assertEqual(result.status, AgentRunStatus.COMPLETED)
        nodes = [node for node, _ in events]
        for expected in ("schema", "plan", "sql_validate", "sql_execute", "analysis"):
            self.assertIn(expected, nodes)

    def test_pipeline_can_cancel_before_schema_retrieval(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            pipeline = AskDataText2SQLPipeline(
                PipelineConfig(db_path=Path(temp_dir) / "trade.db")
            )
            result = pipeline.run("查询销售额", should_cancel=lambda: True)
        self.assertEqual(result.status, AgentRunStatus.CANCELLED)


class ConversationListingTestCase(unittest.TestCase):
    def test_sessions_are_ordered_by_latest_activity(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = SQLiteMemoryStore(Path(temp_dir) / "memory.db")
            store.add_message(session_id="s1", user_id="u1", role=MessageRole.USER, content="第一个问题")
            store.add_message(session_id="s2", user_id="u1", role=MessageRole.USER, content="第二个问题")
            sessions = store.list_sessions(user_id="u1")
        self.assertEqual([item["session_id"] for item in sessions], ["s2", "s1"])
        self.assertEqual(sessions[0]["title"], "第二个问题")


class ApplicationServiceTestCase(unittest.TestCase):
    def test_background_agent_result_is_available_to_web_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            application = AskDataApplicationService(runtime_dir=temp_dir)
            try:
                record = application.submit_chat(
                    user_id="u1",
                    session_id="s1",
                    query="查询总交易笔数大于50000的利率是多少",
                )
                snapshot = RunManagerTestCase._wait_terminal(application.runs, record.run_id)
                self.assertEqual(snapshot["status"], "completed")
                self.assertEqual(snapshot["result"]["schema_version"], "1.0")
                self.assertTrue(snapshot["result"]["sql"]["text"].startswith("SELECT"))
                self.assertEqual(application.list_conversations(user_id="u1")[0]["session_id"], "s1")
                messages = application.get_conversation(user_id="u1", session_id="s1")
                self.assertEqual([item["role"] for item in messages], ["user", "assistant"])
                self.assertTrue(messages[-1]["payload"]["agent_trace"])
                self.assertEqual(
                    {item["node"] for item in messages[-1]["payload"]["agent_trace"]},
                    {
                        "intent",
                        "schema",
                        "plan",
                        "sql_generate",
                        "sql_validate",
                        "sql_execute",
                        "analysis",
                    },
                )
            finally:
                application.close()


if __name__ == "__main__":
    unittest.main()
