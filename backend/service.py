from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any, Dict, Optional

from env_settings import postgres_url
from askdata_memory import ConversationMemoryService, MemoryServiceConfig, PostgresMemoryStore
from askdata_pipeline import AskDataText2SQLPipeline, DynamicAskDataService
from askdata_pipeline.objects import AgentRunStatus

from .run_manager import RunManager, RunRecord
from .data_source import DataSourceManager, DataSourceUnavailableError


class RunAccessError(PermissionError):
    """运行记录存在，但不属于当前用户。"""


class RunNotReadyError(RuntimeError):
    """运行尚未成功完成，不能保存或导出。"""


class AskDataApplicationService:
    """Web API 和 Agent 核心之间的编排层。"""

    def __init__(
        self,
        *,
        pipeline: Optional[AskDataText2SQLPipeline] = None,
        memory: Optional[ConversationMemoryService] = None,
        run_manager: Optional[RunManager] = None,
        data_source_manager: Optional[DataSourceManager] = None,
        runtime_dir: str | Path = "runtime_data",
    ):
        runtime_path = Path(runtime_dir)
        runtime_path.mkdir(parents=True, exist_ok=True)
        self._pipeline_lock = threading.RLock()
        self.data_sources = data_source_manager
        if pipeline is None:
            self.data_sources = self.data_sources or DataSourceManager(runtime_path)
            try:
                pipeline = self.data_sources.get_pipeline()
            except DataSourceUnavailableError:
                pipeline = None
        self.pipeline = pipeline
        if memory is None:
            database_url = postgres_url()
            memory = ConversationMemoryService(
                MemoryServiceConfig(db_path=runtime_path / "memory.db"),
                store=(PostgresMemoryStore(database_url) if database_url else None),
            )
        self.memory = memory
        self.dynamic_service = (
            DynamicAskDataService(self.pipeline, self.memory)
            if self.pipeline is not None
            else None
        )
        self.runs = run_manager or RunManager()

    def submit_chat(
        self,
        *,
        user_id: str,
        session_id: str,
        query: str,
        enable_long_term: bool = False,
        generate_chart: Optional[bool] = None,
        parent_run_id: str = "",
        drill_direction: str = "",
    ) -> RunRecord:
        def handler(record, emit, should_cancel) -> Dict[str, Any]:
            with self._pipeline_lock:
                dynamic_service = self.dynamic_service
            if dynamic_service is None:
                raise DataSourceUnavailableError("数据源尚未连接，请先检查配置并同步 Schema")
            routed = dynamic_service.run(
                user_id=record.user_id,
                session_id=record.session_id,
                query=record.query,
                enable_long_term=enable_long_term,
                run_id=record.run_id,
                event_callback=emit,
                should_cancel=should_cancel,
                generate_chart=generate_chart,
            )
            if routed.pipeline_result is not None:
                payload = routed.pipeline_result.to_api_response()
            else:
                payload = {
                    "schema_version": "1.0",
                    "run_id": record.run_id,
                    "status": AgentRunStatus.COMPLETED.value,
                    "answer": routed.answer,
                    "insights": [],
                    "table": {"columns": [], "rows": []},
                    "charts": [],
                    "sql": {
                        "text": "",
                        "dialect": "",
                        "duration_ms": 0,
                        "validation": {},
                    },
                    "scope": {"route": routed.decision.route.value},
                    "warnings": [],
                    "suggested_questions": [],
                    "error": None,
                }
            if parent_run_id:
                payload.setdefault("scope", {})["drilldown"] = {
                    "parent_run_id": parent_run_id,
                    "direction": drill_direction,
                }
            return payload

        return self.runs.submit(
            user_id=user_id,
            session_id=session_id,
            query=query,
            handler=handler,
        )

    def submit_drilldown(
        self,
        *,
        user_id: str,
        parent_run_id: str,
        query: str,
        direction: str,
        generate_chart: Optional[bool] = None,
    ) -> RunRecord:
        parent = self.runs.get(parent_run_id)
        if parent.user_id != user_id:
            raise RunAccessError(parent_run_id)
        if parent.status != AgentRunStatus.COMPLETED.value or parent.result is None:
            raise RunNotReadyError(parent_run_id)
        return self.submit_chat(
            user_id=user_id,
            session_id=parent.session_id,
            query=query,
            generate_chart=generate_chart,
            parent_run_id=parent_run_id,
            drill_direction=direction,
        )

    def data_source_status(self) -> Dict[str, Any]:
        if self.data_sources is None:
            return {
                "database_type": self.pipeline.config.database_type if self.pipeline else "custom",
                "database": self.pipeline.config.database_name if self.pipeline else "",
                "ready": self.pipeline is not None,
                "connected": self.pipeline is not None,
                "readonly_verified": False,
                "warnings": ["当前使用外部注入的数据源，无法读取管理状态"],
                "error": None,
            }
        return self.data_sources.status()

    def test_data_source(self) -> Dict[str, Any]:
        if self.data_sources is None:
            raise DataSourceUnavailableError("外部注入的数据源不支持连接检测")
        return self.data_sources.test_connection()

    def sync_data_source(self) -> Dict[str, Any]:
        if self.data_sources is None:
            raise DataSourceUnavailableError("外部注入的数据源不支持 Schema 同步")
        status = self.data_sources.sync()
        pipeline = self.data_sources.get_pipeline()
        with self._pipeline_lock:
            self.pipeline = pipeline
            self.dynamic_service = DynamicAskDataService(pipeline, self.memory)
        return status

    def list_conversations(self, *, user_id: str, limit: int = 50):
        return self.memory.store.list_sessions(user_id=user_id, limit=limit)

    def get_conversation(self, *, user_id: str, session_id: str):
        return [
            {
                "id": message.id,
                "session_id": message.session_id,
                "user_id": message.user_id,
                "role": message.role.value,
                "content": message.content,
                "message_type": message.message_type,
                "payload": message.payload,
                "metadata": message.metadata,
                "created_at": message.created_at,
            }
            for message in self.memory.store.list_messages(
                user_id=user_id,
                session_id=session_id,
            )
        ]

    def get_export_result(self, *, user_id: str, run_id: str) -> Dict[str, Any]:
        record = self.runs.get(run_id)
        if record.user_id != user_id:
            raise RunAccessError(run_id)
        if record.status != AgentRunStatus.COMPLETED.value or record.result is None:
            raise RunNotReadyError(run_id)
        return record.result

    def save_analysis(
        self,
        *,
        user_id: str,
        run_id: str,
        title: str = "",
    ) -> Dict[str, Any]:
        record = self.runs.get(run_id)
        result = self.get_export_result(user_id=user_id, run_id=run_id)
        safe_title = title.strip()[:120] or record.query.strip()[:60] or "未命名分析"
        return self.memory.store.save_analysis(
            user_id=user_id,
            session_id=record.session_id,
            run_id=record.run_id,
            title=safe_title,
            query=record.query,
            result=result,
        )

    def list_saved_analyses(self, *, user_id: str, limit: int = 50):
        return self.memory.store.list_saved_analyses(user_id=user_id, limit=limit)

    def get_saved_analysis(self, *, user_id: str, analysis_id: str):
        return self.memory.store.get_saved_analysis(
            user_id=user_id,
            analysis_id=analysis_id,
        )

    def delete_saved_analysis(self, *, user_id: str, analysis_id: str) -> bool:
        return self.memory.store.delete_saved_analysis(
            user_id=user_id,
            analysis_id=analysis_id,
        )

    def create_dashboard(
        self,
        *,
        user_id: str,
        name: str,
        description: str = "",
    ):
        return self.memory.store.create_dashboard(
            user_id=user_id,
            name=name,
            description=description,
        )

    def list_dashboards(self, *, user_id: str, limit: int = 50):
        return self.memory.store.list_dashboards(user_id=user_id, limit=limit)

    def get_dashboard(self, *, user_id: str, dashboard_id: str):
        return self.memory.store.get_dashboard(
            user_id=user_id,
            dashboard_id=dashboard_id,
        )

    def add_dashboard_card(
        self,
        *,
        user_id: str,
        dashboard_id: str,
        analysis_id: str,
        title: str = "",
    ):
        return self.memory.store.add_dashboard_card(
            user_id=user_id,
            dashboard_id=dashboard_id,
            analysis_id=analysis_id,
            title=title,
        )

    def remove_dashboard_card(
        self,
        *,
        user_id: str,
        dashboard_id: str,
        card_id: str,
    ) -> bool:
        return self.memory.store.remove_dashboard_card(
            user_id=user_id,
            dashboard_id=dashboard_id,
            card_id=card_id,
        )

    def delete_dashboard(self, *, user_id: str, dashboard_id: str) -> bool:
        return self.memory.store.delete_dashboard(
            user_id=user_id,
            dashboard_id=dashboard_id,
        )

    def close(self) -> None:
        self.runs.shutdown(wait=True)
        self.memory.close(wait=True)
