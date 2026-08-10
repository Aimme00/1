from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from askdata_memory import (
    ConversationMemoryContext,
    ConversationMemoryService,
    MemoryMessage,
    MemoryServiceConfig,
    MessageRole,
    ShortTermContext,
)
from askdata_pipeline import (
    DynamicAskDataService,
    DynamicIntentRouter,
    ModelIntentClassifier,
    PipelineResult,
    RouteType,
    RuleBasedDataQAClient,
    RuleBasedIntentClassifier,
    StepExecutionLog,
)


def build_context(*messages: MemoryMessage) -> ConversationMemoryContext:
    return ConversationMemoryContext(
        short_term=ShortTermContext(
            session_id="s1",
            user_id="u1",
            messages=list(messages),
        )
    )


def sql_result_message() -> MemoryMessage:
    return MemoryMessage(
        id=1,
        session_id="s1",
        user_id="u1",
        role=MessageRole.ASSISTANT,
        content="已完成数据库查询",
        message_type="sql_result",
        payload={
            "step_logs": [
                {
                    "execution_result": {
                        "success": True,
                        "columns": ["interest_rate"],
                        "rows": [
                            {"interest_rate": 4.58},
                            {"interest_rate": 4.95},
                        ],
                    }
                }
            ]
        },
    )


class InvalidModelClient:
    def generate(self, prompt: str) -> str:
        return "这不是合法 JSON"


class FakePipeline:
    def __init__(self) -> None:
        self.call_count = 0
        self.contexts: list[str] = []

    def run(self, query: str, keywords=None, conversation_context: str = "") -> PipelineResult:
        self.call_count += 1
        self.contexts.append(conversation_context)
        return PipelineResult(
            query=query,
            keywords=["利率"],
            schema_context="数据库：trade_db\n表名：interest_info\n- 字段名：interest_rate",
            cot_output="查询利率",
            step_logs=[
                StepExecutionLog(
                    database="trade_db",
                    cot_step=object(),
                    local_schema="interest_info.interest_rate",
                    sql="SELECT interest_rate FROM interest_info;",
                    execution_request={
                        "database": "trade_db",
                        "sql": "SELECT interest_rate FROM interest_info;",
                    },
                    execution_result={
                        "success": True,
                        "columns": ["interest_rate"],
                        "rows": [{"interest_rate": 4.58}, {"interest_rate": 4.95}],
                    },
                )
            ],
        )


class RoutingTestCase(unittest.TestCase):
    def test_new_business_data_request_routes_to_database(self) -> None:
        decision = RuleBasedIntentClassifier().classify(
            "查询总交易笔数大于50000的利率是多少",
            build_context(),
        )
        self.assertEqual(decision.route, RouteType.DATABASE_QUERY)
        self.assertTrue(decision.needs_database)

    def test_metric_definition_routes_to_data_qa(self) -> None:
        decision = RuleBasedIntentClassifier().classify(
            "什么是年化利率，它的业务口径是什么？",
            build_context(),
        )
        self.assertEqual(decision.route, RouteType.DATA_QA)
        self.assertFalse(decision.needs_database)

    def test_historical_result_follow_up_does_not_query_database(self) -> None:
        decision = RuleBasedIntentClassifier().classify(
            "帮我分析一下刚才的查询结果",
            build_context(sql_result_message()),
        )
        self.assertEqual(decision.route, RouteType.DATA_QA)
        self.assertIn("result_reference", decision.signals)

    def test_explicit_fresh_query_still_routes_to_database(self) -> None:
        decision = RuleBasedIntentClassifier().classify(
            "重新查询今天最新的交易利率",
            build_context(sql_result_message()),
        )
        self.assertEqual(decision.route, RouteType.DATABASE_QUERY)
        self.assertIn("fresh_data", decision.signals)

    def test_invalid_model_output_falls_back_to_rules(self) -> None:
        router = DynamicIntentRouter(
            model_classifier=ModelIntentClassifier(InvalidModelClient())
        )
        decision = router.route("统计今天的交易笔数", build_context())
        self.assertEqual(decision.route, RouteType.DATABASE_QUERY)
        self.assertNotIn("model_classifier", decision.signals)

    def test_rule_based_data_qa_reuses_latest_sql_result(self) -> None:
        answer = RuleBasedDataQAClient().answer(
            "总结一下刚才的查询结果",
            build_context(sql_result_message()),
        )
        self.assertIn("4.58", answer)
        self.assertIn("4.95", answer)
        self.assertIn("平均值", answer)

    def test_dynamic_service_only_calls_pipeline_for_database_route(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            memory = ConversationMemoryService(
                MemoryServiceConfig(
                    db_path=Path(temp_dir) / "memory.db",
                    embedding_dimensions=128,
                    use_model_summarizer_when_available=False,
                    use_reranker_when_available=False,
                )
            )
            pipeline = FakePipeline()
            service = DynamicAskDataService(
                pipeline=pipeline,
                memory=memory,
                router=DynamicIntentRouter(),
                data_qa_client=RuleBasedDataQAClient(),
                use_model_when_available=False,
            )
            try:
                database_response = service.run(
                    user_id="u1",
                    session_id="s1",
                    query="查询交易利率是多少",
                )
                self.assertEqual(database_response.decision.route, RouteType.DATABASE_QUERY)
                self.assertTrue(database_response.queried_database)
                self.assertEqual(pipeline.call_count, 1)

                qa_response = service.run(
                    user_id="u1",
                    session_id="s1",
                    query="分析一下刚才的查询结果",
                )
                self.assertEqual(qa_response.decision.route, RouteType.DATA_QA)
                self.assertFalse(qa_response.queried_database)
                self.assertEqual(pipeline.call_count, 1)
                self.assertIn("4.58", qa_response.answer)

                messages = memory.store.list_messages(user_id="u1", session_id="s1")
                self.assertEqual(messages[-1].message_type, "data_qa")
                self.assertEqual(
                    messages[-1].metadata["route_decision"]["route"],
                    RouteType.DATA_QA.value,
                )
            finally:
                memory.close()


if __name__ == "__main__":
    unittest.main()
