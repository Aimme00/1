from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional, Sequence

from .long_term import (
    LocalMemoryEmbeddingClient,
    LongTermMemoryConfig,
    LongTermMemoryService,
    MemoryReranker,
)
from .objects import (
    ConversationMemoryContext,
    LongTermMemory,
    MemoryHit,
    MemoryKind,
    MemoryMessage,
    MessageRole,
)
from .short_term import ShortTermMemoryConfig, ShortTermMemoryService
from .storage import SQLiteMemoryStore
from .summarizer import (
    ExtractiveMemorySummarizer,
    MemorySummarizer,
    ModelMemorySummarizer,
)
from .reranker import AliyunMemoryReranker
from .vector_store import MilvusLongTermMemoryConfig, MilvusLongTermVectorIndex


@dataclass
class MemoryServiceConfig:
    db_path: str | Path = "runtime_data/memory.db"
    embedding_dimensions: int = 1024
    use_model_summarizer_when_available: bool = True
    use_reranker_when_available: bool = True
    milvus_uri: str = ""
    milvus_collection_name: str = "askdata_long_term_memory"
    short_term: ShortTermMemoryConfig = field(default_factory=ShortTermMemoryConfig)
    long_term: LongTermMemoryConfig = field(default_factory=LongTermMemoryConfig)


class ConversationMemoryService:
    """供 API/路由层调用的统一长短期记忆入口。"""

    def __init__(
        self,
        config: Optional[MemoryServiceConfig] = None,
        *,
        store: Optional[object] = None,
        summarizer: Optional[MemorySummarizer] = None,
        embedding_client: Any = None,
        reranker: Optional[MemoryReranker] = None,
    ):
        self.config = config or MemoryServiceConfig()
        self.store = store or SQLiteMemoryStore(self.config.db_path)
        self.summarizer = summarizer or self._build_default_summarizer()
        resolved_embedding = embedding_client or LocalMemoryEmbeddingClient(
            dimensions=self.config.embedding_dimensions
        )
        self.short_term = ShortTermMemoryService(
            store=self.store,
            summarizer=self.summarizer,
            config=self.config.short_term,
        )
        resolved_reranker = reranker or self._build_default_reranker()
        vector_index = self._build_vector_index()
        self.long_term = LongTermMemoryService(
            store=self.store,
            embedding_client=resolved_embedding,
            summarizer=self.summarizer,
            reranker=resolved_reranker,
            vector_index=vector_index,
            config=self.config.long_term,
        )

    def begin_user_turn(
        self,
        *,
        user_id: str,
        session_id: str,
        query: str,
        enable_long_term: bool = False,
        long_term_top_k: Optional[int] = None,
        memory_kinds: Optional[Sequence[MemoryKind]] = None,
        metadata_filters: Optional[Dict[str, Any]] = None,
    ) -> ConversationMemoryContext:
        self.short_term.add_message(
            user_id=user_id,
            session_id=session_id,
            role=MessageRole.USER,
            content=query,
        )
        return self.get_context(
            user_id=user_id,
            session_id=session_id,
            query=query,
            enable_long_term=enable_long_term,
            long_term_top_k=long_term_top_k,
            memory_kinds=memory_kinds,
            metadata_filters=metadata_filters,
        )

    def get_context(
        self,
        *,
        user_id: str,
        session_id: str,
        query: str = "",
        enable_long_term: bool = False,
        long_term_top_k: Optional[int] = None,
        memory_kinds: Optional[Sequence[MemoryKind]] = None,
        metadata_filters: Optional[Dict[str, Any]] = None,
    ) -> ConversationMemoryContext:
        short_context = self.short_term.get_context(user_id=user_id, session_id=session_id)
        hits: list[MemoryHit] = []
        if enable_long_term and query.strip():
            hits = self.long_term.recall(
                user_id=user_id,
                query=query,
                top_k=long_term_top_k,
                kinds=memory_kinds,
                metadata_filters=metadata_filters,
            )
        return ConversationMemoryContext(
            short_term=short_context,
            long_term_hits=hits,
            long_term_enabled=enable_long_term,
        )

    def record_assistant_message(
        self,
        *,
        user_id: str,
        session_id: str,
        content: str,
        message_type: str = "text",
        payload: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> MemoryMessage:
        return self.short_term.add_message(
            user_id=user_id,
            session_id=session_id,
            role=MessageRole.ASSISTANT,
            content=content,
            message_type=message_type,
            payload=payload,
            metadata=metadata,
        )

    def save_structured_result(
        self,
        *,
        user_id: str,
        query: str,
        result: Any,
        database: str = "",
        tables: Optional[list[str]] = None,
        columns: Optional[list[str]] = None,
        filters: Optional[Dict[str, Any]] = None,
        cot: str = "",
        sql: str | list[str] = "",
        extra_metadata: Optional[Dict[str, Any]] = None,
    ) -> LongTermMemory:
        """仅在用户明确点击/请求保存时调用。"""
        return self.long_term.save_structured(
            user_id=user_id,
            query=query,
            result=result,
            database=database,
            tables=tables,
            columns=columns,
            filters=filters,
            cot=cot,
            sql=sql,
            extra_metadata=extra_metadata,
        )

    def save_unstructured_content(
        self,
        *,
        user_id: str,
        content: Any,
        title: str = "",
        extra_metadata: Optional[Dict[str, Any]] = None,
    ) -> LongTermMemory:
        """仅在用户明确点击/请求保存时调用。"""
        return self.long_term.save_unstructured(
            user_id=user_id,
            content=content,
            title=title,
            extra_metadata=extra_metadata,
        )

    def save_pipeline_result(self, *, user_id: str, result: Any) -> LongTermMemory:
        result_dict = result.to_dict()
        sql_list = [log.sql for log in result.step_logs]
        where_conditions = []
        for sql in sql_list:
            match = re.search(r"\bWHERE\b(.*?)(?:;|$)", sql, flags=re.IGNORECASE | re.DOTALL)
            if match:
                where_conditions.append(" ".join(match.group(1).split()))
        tables = re.findall(r"表名：([^\n]+)", result.schema_context)
        columns = re.findall(r"字段名：([^\n]+)", result.schema_context)
        database = result.step_logs[0].database if result.step_logs else ""
        return self.save_structured_result(
            user_id=user_id,
            query=result.query,
            result=result_dict,
            database=database,
            tables=list(dict.fromkeys(tables)),
            columns=list(dict.fromkeys(columns)),
            filters={"sql_where": where_conditions},
            cot=result.cot_output,
            sql=sql_list,
            extra_metadata={"source": "text2sql_pipeline"},
        )

    def delete_long_term_memory(self, *, user_id: str, memory_id: str) -> bool:
        return self.long_term.delete(user_id=user_id, memory_id=memory_id)

    def close(self, wait: bool = True) -> None:
        self.short_term.close(wait=wait)

    def _build_default_summarizer(self) -> MemorySummarizer:
        if self.config.use_model_summarizer_when_available and os.getenv("DASHSCOPE_API_KEY"):
            from cot_planning import ThinkingModelClient, ThinkingModelConfig

            return ModelMemorySummarizer(
                ThinkingModelClient(
                    ThinkingModelConfig(
                        temperature=0.0,
                        use_mock_when_no_api_key=False,
                    )
                )
            )
        return ExtractiveMemorySummarizer()

    def _build_default_reranker(self) -> Optional[MemoryReranker]:
        if not self.config.use_reranker_when_available:
            return None
        if not os.getenv("DASHSCOPE_API_KEY") or not os.getenv("DASHSCOPE_WORKSPACE_ID"):
            return None
        from schema_retrieval.rerank_client import AliyunRerankClient, AliyunRerankConfig

        return AliyunMemoryReranker(
            AliyunRerankClient(
                AliyunRerankConfig(
                    instruct=(
                        "Given a user question, rank personal memory summaries "
                        "by relevance and factual usefulness."
                    )
                )
            )
        )

    def _build_vector_index(self):
        if not self.config.milvus_uri:
            return None
        return MilvusLongTermVectorIndex(
            MilvusLongTermMemoryConfig(
                uri=self.config.milvus_uri,
                collection_name=self.config.milvus_collection_name,
                embedding_dimensions=self.config.embedding_dimensions,
            )
        )
