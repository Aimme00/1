from __future__ import annotations

import json
import inspect
from dataclasses import dataclass
from typing import Callable, Optional

from askdata_memory import ConversationMemoryContext, ConversationMemoryService, LongTermMemory

from .objects import PipelineResult
from .text2sql_pipeline import AskDataText2SQLPipeline


@dataclass
class ConversationPipelineResult:
    pipeline_result: PipelineResult
    memory_context: ConversationMemoryContext
    answer: str = ""


class MemoryAwareAskDataService:
    """把记忆上下文接入现有 Text2SQL 流程的轻量会话编排层。"""

    def __init__(
        self,
        pipeline: AskDataText2SQLPipeline,
        memory: ConversationMemoryService,
    ):
        self.pipeline = pipeline
        self.memory = memory

    def run(
        self,
        *,
        user_id: str,
        session_id: str,
        query: str,
        enable_long_term: bool = False,
        long_term_top_k: Optional[int] = None,
        run_id: Optional[str] = None,
        event_callback: Optional[Callable] = None,
        should_cancel: Optional[Callable[[], bool]] = None,
        generate_chart: Optional[bool] = None,
    ) -> ConversationPipelineResult:
        context = self.memory.begin_user_turn(
            user_id=user_id,
            session_id=session_id,
            query=query,
            enable_long_term=enable_long_term,
            long_term_top_k=long_term_top_k,
        )
        return self.run_with_context(
            user_id=user_id,
            session_id=session_id,
            query=query,
            context=context,
            run_id=run_id,
            event_callback=event_callback,
            should_cancel=should_cancel,
            generate_chart=generate_chart,
        )

    def run_with_context(
        self,
        *,
        user_id: str,
        session_id: str,
        query: str,
        context: ConversationMemoryContext,
        assistant_metadata: Optional[dict] = None,
        run_id: Optional[str] = None,
        event_callback: Optional[Callable] = None,
        should_cancel: Optional[Callable[[], bool]] = None,
        generate_chart: Optional[bool] = None,
    ) -> ConversationPipelineResult:
        """使用已准备的记忆上下文执行查询，供动态路由服务复用。"""
        optional_kwargs = {
            "run_id": run_id,
            "user_id": user_id,
            "session_id": session_id,
            "event_callback": event_callback,
            "should_cancel": should_cancel,
            "generate_chart": generate_chart,
        }
        supported = inspect.signature(self.pipeline.run).parameters
        pipeline_kwargs = {
            key: value for key, value in optional_kwargs.items() if key in supported
        }
        result = self.pipeline.run(
            query,
            conversation_context=context.to_prompt_context(),
            **pipeline_kwargs,
        )
        payload = result.to_dict()
        result_preview = json.dumps(
            [log.execution_result for log in result.step_logs],
            ensure_ascii=False,
            default=str,
        )
        if len(result_preview) > 1500:
            result_preview = result_preview[:1490] + "……"
        answer = result.final_answer or f"已完成数据库查询。查询结果：{result_preview}"
        metadata = {
            "sql": [log.sql for log in result.step_logs],
            "database": [log.database for log in result.step_logs],
            "run_id": result.run_id,
            "quality": result.quality_result,
        }
        metadata.update(assistant_metadata or {})
        self.memory.record_assistant_message(
            user_id=user_id,
            session_id=session_id,
            content=answer,
            message_type="sql_result",
            payload=payload,
            metadata=metadata,
        )
        return ConversationPipelineResult(
            pipeline_result=result,
            memory_context=context,
            answer=answer,
        )

    def save_result_to_personal_knowledge_base(
        self, *, user_id: str, result: PipelineResult
    ) -> LongTermMemory:
        """UI 中的“保存到个人知识库”动作应调用此方法。"""
        return self.memory.save_pipeline_result(user_id=user_id, result=result)
