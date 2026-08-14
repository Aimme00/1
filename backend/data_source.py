from __future__ import annotations

import json
import os
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from env_settings import env_bool, env_int, env_text, postgres_url
from askdata_pipeline import AskDataText2SQLPipeline
from askdata_pipeline.local_clients import LocalHashEmbeddingClient
from askdata_pipeline.objects import PipelineConfig
from mcp_router import (
    MySQLExecutorConfig,
    MySQLQueryExecutor,
    PostgresExecutorConfig,
    PostgresQueryExecutor,
)
from schema_retrieval import MySQLSchemaLoader, PostgresSchemaLoader
from schema_retrieval.hybrid_schema_retrieval_service import (
    HybridSchemaRetrievalConfig,
    HybridSchemaRetrievalService,
)
from schema_retrieval.rerank_client import AliyunRerankClient, AliyunRerankConfig
from schema_retrieval.rrf_fusion_client import RRFFusionConfig


class DataSourceUnavailableError(RuntimeError):
    pass


@dataclass(frozen=True)
class DataSourceSettings:
    database_type: str = "sqlite"
    database_alias: str = "trade_db"
    schema_sample_size: int = 0
    enforce_readonly: bool = True
    require_sqlglot: bool = False
    business_meta_path: str = ""

    @classmethod
    def from_environment(cls) -> "DataSourceSettings":
        configured_type = env_text(
            "ASKDATA_DATABASE_TYPE", "postgres" if postgres_url() else "sqlite"
        ).lower()
        database_type = {
            "postgresql": "postgres",
            "neon": "postgres",
            "d1": "sqlite",
        }.get(configured_type, configured_type)
        if database_type not in {"sqlite", "mysql", "postgres"}:
            database_type = "postgres" if postgres_url() else "sqlite"
        default_alias = (
            env_text("ASKDATA_MYSQL_DATABASE", "analytics")
            if database_type == "mysql"
            else "trade_db"
        )
        return cls(
            database_type=database_type,
            database_alias=env_text("ASKDATA_DATABASE_ALIAS", default_alias),
            schema_sample_size=env_int(
                "ASKDATA_SCHEMA_SAMPLE_SIZE", 0, minimum=0, maximum=10
            ),
            enforce_readonly=env_bool("ASKDATA_ENFORCE_READONLY", True),
            require_sqlglot=env_bool(
                "ASKDATA_REQUIRE_SQLGLOT", database_type in {"mysql", "postgres"}
            ),
            business_meta_path=env_text("ASKDATA_BUSINESS_META_PATH"),
        )


class DataSourceManager:
    """构建并原子切换 Agent 使用的数据源 Pipeline。"""

    def __init__(
        self,
        runtime_dir: str | Path,
        settings: Optional[DataSourceSettings] = None,
    ):
        self.runtime_dir = Path(runtime_dir)
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        self.settings = settings or DataSourceSettings.from_environment()
        self._lock = threading.RLock()
        self._pipeline: Optional[AskDataText2SQLPipeline] = None
        self._status: Dict[str, Any] = self._empty_status()
        try:
            self.sync()
        except Exception as exc:
            self._status.update(
                ready=False,
                connected=False,
                error=self._safe_error(exc),
            )

    def get_pipeline(self) -> AskDataText2SQLPipeline:
        with self._lock:
            if self._pipeline is None:
                raise DataSourceUnavailableError(
                    self._status.get("error") or "数据源尚未准备完成"
                )
            return self._pipeline

    def status(self) -> Dict[str, Any]:
        with self._lock:
            return dict(self._status)

    def test_connection(self) -> Dict[str, Any]:
        if self.settings.database_type == "sqlite":
            db_path = self.runtime_dir / "trade_demo.db"
            with sqlite3.connect(str(db_path), timeout=5) as connection:
                connection.execute("SELECT 1").fetchone()
            return {
                "connected": True,
                "database": self.settings.database_alias,
                "database_type": "sqlite",
                "readonly_verified": True,
                "select_granted": True,
                "warnings": [],
            }

        loader = self._mysql_loader() if self.settings.database_type == "mysql" else self._postgres_loader()
        report = loader.test_connection().to_dict()
        report["database_type"] = self.settings.database_type
        return report

    def sync(self) -> Dict[str, Any]:
        try:
            return self._sync()
        except Exception as exc:
            with self._lock:
                self._status["error"] = self._safe_error(exc)
                if self._pipeline is None:
                    self._status["ready"] = False
                    self._status["connected"] = False
                else:
                    warnings = list(self._status.get("warnings") or [])
                    notice = "最近一次 Schema 同步失败，当前继续使用上次成功版本"
                    if notice not in warnings:
                        warnings.append(notice)
                    self._status["warnings"] = warnings
            raise

    def _sync(self) -> Dict[str, Any]:
        if self.settings.database_type == "sqlite":
            pipeline = AskDataText2SQLPipeline(
                PipelineConfig(
                    database_name=self.settings.database_alias,
                    db_path=self.runtime_dir / "trade_demo.db",
                    database_type="sqlite",
                    sql_dialect="sqlite",
                    bootstrap_demo_database=True,
                    require_sqlglot=self.settings.require_sqlglot,
                )
            )
            retrieval = pipeline.schema_retrieval_service
            status = self._ready_status(
                database=self.settings.database_alias,
                table_count=len(retrieval.tables),
                column_count=len(retrieval.columns),
                relation_count=len(retrieval.relations),
                readonly_verified=True,
                warnings=[],
            )
        elif self.settings.database_type == "mysql":
            loader = self._mysql_loader()
            report = loader.test_connection()
            if self.settings.enforce_readonly and not report.readonly_verified:
                detail = "；".join(report.warnings) or "无法确认只读权限"
                raise DataSourceUnavailableError(f"MySQL 账号未通过只读校验：{detail}")
            tables, columns, relations = loader.load()
            business_meta = self._load_business_meta()
            retrieval = HybridSchemaRetrievalService(
                tables=tables,
                columns=columns,
                relations=relations,
                business_meta=business_meta,
                embedding_client=LocalHashEmbeddingClient(dimensions=1024),
                rerank_client=AliyunRerankClient(
                    AliyunRerankConfig(api_key="", workspace_id="", model="qwen-rerank")
                ),
                keyword_extractor=None,
                config=self._retrieval_config(),
            )
            mysql_config = MySQLExecutorConfig.from_env()
            pipeline = AskDataText2SQLPipeline(
                PipelineConfig(
                    database_name=self.settings.database_alias,
                    database_type="mysql",
                    sql_dialect="mysql",
                    bootstrap_demo_database=False,
                    max_query_rows=mysql_config.max_rows,
                    require_sqlglot=self.settings.require_sqlglot,
                ),
                schema_retrieval_service=retrieval,
                query_executor=MySQLQueryExecutor(
                    mysql_config,
                    database_alias=self.settings.database_alias,
                ),
                business_meta=business_meta,
            )
            self._write_snapshot(tables, columns, relations)
            status = self._ready_status(
                database=mysql_config.database,
                table_count=len(tables),
                column_count=len(columns),
                relation_count=len(relations),
                readonly_verified=report.readonly_verified,
                warnings=list(report.warnings),
            )
        else:
            loader = self._postgres_loader()
            report = loader.test_connection()
            tables, columns, relations = loader.load()
            business_meta = self._load_business_meta()
            if not business_meta:
                from askdata_pipeline.demo_data import get_trade_business_meta

                business_meta = get_trade_business_meta()
                loader.business_meta = business_meta
                tables, columns, relations = loader.load()
            retrieval = HybridSchemaRetrievalService(
                tables=tables,
                columns=columns,
                relations=relations,
                business_meta=business_meta,
                embedding_client=LocalHashEmbeddingClient(dimensions=1024),
                rerank_client=AliyunRerankClient(
                    AliyunRerankConfig(api_key="", workspace_id="", model="qwen-rerank")
                ),
                keyword_extractor=None,
                config=self._retrieval_config(),
            )
            postgres_config = PostgresExecutorConfig.from_env()
            pipeline = AskDataText2SQLPipeline(
                PipelineConfig(
                    database_name=self.settings.database_alias,
                    database_type="postgres",
                    sql_dialect="postgres",
                    bootstrap_demo_database=False,
                    max_query_rows=postgres_config.max_rows,
                    require_sqlglot=self.settings.require_sqlglot,
                ),
                schema_retrieval_service=retrieval,
                query_executor=PostgresQueryExecutor(
                    postgres_config,
                    database_alias=self.settings.database_alias,
                ),
                business_meta=business_meta,
            )
            status = self._ready_status(
                database=report.database,
                table_count=len(tables),
                column_count=len(columns),
                relation_count=len(relations),
                readonly_verified=report.readonly_verified,
                warnings=list(report.warnings),
            )

        with self._lock:
            self._pipeline = pipeline
            self._status = status
            return dict(status)

    def _mysql_loader(self) -> MySQLSchemaLoader:
        return MySQLSchemaLoader(
            MySQLExecutorConfig.from_env(),
            database_name=self.settings.database_alias,
            business_meta=self._load_business_meta(),
            sample_size=self.settings.schema_sample_size,
        )

    def _postgres_loader(self) -> PostgresSchemaLoader:
        business_meta = self._load_business_meta()
        if not business_meta:
            from askdata_pipeline.demo_data import get_trade_business_meta

            business_meta = get_trade_business_meta()
        return PostgresSchemaLoader(
            PostgresExecutorConfig.from_env(),
            database_name=self.settings.database_alias,
            business_meta=business_meta,
            sample_size=self.settings.schema_sample_size,
        )

    def _load_business_meta(self) -> Dict[str, Any]:
        if not self.settings.business_meta_path:
            return {}
        path = Path(self.settings.business_meta_path)
        if not path.is_file():
            raise FileNotFoundError(f"业务元数据文件不存在：{path}")
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("业务元数据必须是 JSON 对象")
        return data

    def _write_snapshot(self, tables, columns, relations) -> None:
        payload = {
            "schema_version": "1.0",
            "database_type": self.settings.database_type,
            "database": self.settings.database_alias,
            "synced_at": self._now(),
            "tables": [
                {
                    "name": item.table_name,
                    "description": item.description,
                    "aliases": item.aliases,
                    "primary_keys": item.primary_keys,
                }
                for item in tables.values()
            ],
            "columns": [
                {
                    "table": item.table_name,
                    "name": item.column_name,
                    "data_type": item.data_type,
                    "nullable": item.nullable,
                    "description": item.description,
                    "aliases": item.aliases,
                    "semantic_role": item.semantic_role,
                    "is_primary_key": item.is_primary_key,
                    "foreign_key_ref": item.foreign_key_ref,
                }
                for item in columns
            ],
            "relations": [
                {
                    "source_table": item.source_table,
                    "source_column": item.source_column,
                    "target_table": item.target_table,
                    "target_column": item.target_column,
                }
                for item in relations
            ],
        }
        destination = self.runtime_dir / "mysql_schema_snapshot.json"
        temporary = destination.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(destination)

    def _ready_status(
        self,
        *,
        database: str,
        table_count: int,
        column_count: int,
        relation_count: int,
        readonly_verified: bool,
        warnings,
    ) -> Dict[str, Any]:
        return {
            "database_type": self.settings.database_type,
            "database": database,
            "alias": self.settings.database_alias,
            "ready": True,
            "connected": True,
            "readonly_verified": readonly_verified,
            "table_count": table_count,
            "column_count": column_count,
            "relation_count": relation_count,
            "sample_values_enabled": self.settings.schema_sample_size > 0,
            "last_synced_at": self._now(),
            "warnings": list(warnings),
            "error": None,
        }

    def _empty_status(self) -> Dict[str, Any]:
        return {
            "database_type": self.settings.database_type,
            "database": "",
            "alias": self.settings.database_alias,
            "ready": False,
            "connected": False,
            "readonly_verified": False,
            "table_count": 0,
            "column_count": 0,
            "relation_count": 0,
            "sample_values_enabled": self.settings.schema_sample_size > 0,
            "last_synced_at": None,
            "warnings": [],
            "error": None,
        }

    @staticmethod
    def _retrieval_config() -> HybridSchemaRetrievalConfig:
        return HybridSchemaRetrievalConfig(
            per_keyword_top_k=20,
            include_join_columns=True,
            rrf_config=RRFFusionConfig(
                rrf_k=60,
                truncate_multiplier=6,
                min_fused_top_k=10,
                max_fused_top_k=50,
                final_top_k=20,
                route_weights={"keyword": 1.0, "vector": 1.0},
            ),
            rerank_top_multiplier=6,
            rerank_min_top_n=12,
            rerank_max_top_n=20,
        )

    @staticmethod
    def _safe_error(exc: Exception) -> str:
        message = str(exc).replace("\n", " ").strip()
        return message[:500] or exc.__class__.__name__

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()
