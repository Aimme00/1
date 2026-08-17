from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional
import uuid

from .serialization import json_safe


class AgentRunStatus(str, Enum):
    """Agent 单次运行状态。"""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class PipelineConfig:
    """端到端流程配置。"""

    database_name: str = "trade_db"
    db_path: str | Path = "runtime_data/trade_demo.db"
    database_type: str = "sqlite"
    sql_dialect: str = "sqlite"
    sample_size: int = 5
    bootstrap_demo_database: bool = True
    max_query_rows: int = 5000
    max_sql_repair_attempts: int = 2
    max_execution_repair_attempts: int = 1
    require_sqlglot: bool = False
    auto_generate_charts: bool = False


@dataclass
class StepExecutionLog:
    """单个 CoT 步骤执行日志。"""

    database: str
    cot_step: object
    local_schema: str
    sql: str
    execution_request: Dict[str, str]
    execution_result: Dict[str, Any]
    validation_result: Dict[str, Any] = field(default_factory=dict)
    sql_attempts: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class AgentState:
    """贯穿 Agent 各节点的统一状态，也是 API 层的基础数据结构。"""

    query: str
    keywords: List[str] = field(default_factory=list)
    schema_context: str = ""
    cot_output: str = ""
    step_logs: List[StepExecutionLog] = field(default_factory=list)
    run_id: str = field(default_factory=lambda: f"run_{uuid.uuid4().hex}")
    user_id: str = ""
    session_id: str = ""
    status: AgentRunStatus = AgentRunStatus.PENDING
    intent: Dict[str, Any] = field(default_factory=dict)
    plan: List[Dict[str, Any]] = field(default_factory=list)
    generated_sql: str = ""
    validation_result: Dict[str, Any] = field(default_factory=dict)
    query_result: Dict[str, Any] = field(default_factory=dict)
    analysis_result: Dict[str, Any] = field(default_factory=dict)
    quality_result: Dict[str, Any] = field(default_factory=dict)
    insights: List[Dict[str, Any]] = field(default_factory=list)
    table: Dict[str, Any] = field(default_factory=dict)
    chart_configs: List[Dict[str, Any]] = field(default_factory=list)
    final_answer: str = ""
    scope: Dict[str, Any] = field(default_factory=dict)
    suggested_questions: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    error: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典。"""
        return json_safe({
            "schema_version": "1.0",
            "run_id": self.run_id,
            "user_id": self.user_id,
            "session_id": self.session_id,
            "status": self.status.value,
            "query": self.query,
            "keywords": self.keywords,
            "schema_context": self.schema_context,
            "cot_output": self.cot_output,
            "intent": self.intent,
            "plan": self.plan,
            "generated_sql": self.generated_sql,
            "validation_result": self.validation_result,
            "query_result": self.query_result,
            "analysis_result": self.analysis_result,
            "quality_result": self.quality_result,
            "insights": self.insights,
            "table": self.table,
            "chart_configs": self.chart_configs,
            "final_answer": self.final_answer,
            "scope": self.scope,
            "suggested_questions": self.suggested_questions,
            "warnings": self.warnings,
            "error": self.error,
            "step_logs": [
                {
                    "database": log.database,
                    "sql": log.sql,
                    "validation_result": log.validation_result,
                    "sql_attempts": log.sql_attempts,
                    "execution_request": log.execution_request,
                    "execution_result": log.execution_result,
                }
                for log in self.step_logs
            ],
        })

    def to_api_response(self) -> Dict[str, Any]:
        """返回稳定的前端协议，隐藏内部 Prompt 和完整 Schema 上下文。"""
        duration_ms = int(self.query_result.get("duration_ms") or 0)
        row_count = int(self.query_result.get("row_count") or 0)
        validation_ok = bool(
            self.validation_result.get("is_valid", self.validation_result.get("valid", True))
        )
        public_plan = [
            {
                "step": index,
                "operation": item.get("operation_instruction", ""),
                "output": item.get("output_target", ""),
            }
            for index, item in enumerate(self.plan, start=1)
        ]
        validation_tables = list(self.validation_result.get("tables") or [])
        validation_columns = list(self.validation_result.get("columns") or [])
        validation_issues = [
            {
                "code": issue.get("code", ""),
                "message": issue.get("message", ""),
                "level": issue.get("level", ""),
            }
            for issue in (self.validation_result.get("issues") or [])
            if isinstance(issue, dict)
        ]
        sql_attempt_count = sum(len(log.sql_attempts) for log in self.step_logs)
        analysis_details = {
            "answer": self.final_answer,
            "insight_count": len(self.insights),
            "chart_count": len(self.chart_configs),
        }
        agent_trace = [
            {
                "node": "intent",
                "label": "意图识别",
                "status": "completed",
                "message": "已识别为只读数据库分析任务",
                "details": {
                    "question": self.query,
                    "task_type": "只读数据库分析",
                },
            },
            {
                "node": "schema",
                "label": "Schema 检索",
                "status": "completed",
                "message": "已定位查询所需的数据表与字段",
                "details": {
                    "keywords": self.keywords,
                    "tables": validation_tables,
                    "columns": validation_columns,
                },
            },
            {"node": "plan", "label": "分析规划", "status": "completed" if public_plan else "failed", "message": f"已生成 {len(public_plan)} 个结构化执行步骤", "details": {"steps": public_plan}},
            {
                "node": "sql_generate",
                "label": "SQL 生成",
                "status": "completed" if self.generated_sql else "failed",
                "message": "已根据分析计划生成查询语句" if self.generated_sql else "未生成查询语句",
                "details": {
                    "sql": self.generated_sql,
                    "dialect": self.scope.get("sql_dialect", ""),
                },
            },
            {
                "node": "sql_validate",
                "label": "SQL 校验",
                "status": "completed" if validation_ok else "failed",
                "message": "SQL 只读安全校验通过" if validation_ok else "SQL 安全校验未通过",
                "details": {
                    "valid": validation_ok,
                    "parser": self.validation_result.get("parser", ""),
                    "tables": validation_tables,
                    "attempt_count": sql_attempt_count,
                    "issues": validation_issues,
                },
            },
            {
                "node": "sql_execute",
                "label": "执行查询",
                "status": "completed" if self.query_result.get("success", True) else "failed",
                "message": f"查询完成，返回 {row_count} 行数据",
                "details": {
                    "database": self.scope.get("database", self.query_result.get("database", "")),
                    "row_count": row_count,
                    "duration_ms": duration_ms,
                    "columns": self.query_result.get("columns") or [],
                    "truncated": bool(self.query_result.get("truncated", False)),
                },
            },
            {
                "node": "analysis",
                "label": "结果分析",
                "status": "completed" if self.status == AgentRunStatus.COMPLETED else "failed",
                "message": "已基于查询结果生成描述性结论",
                "details": analysis_details,
            },
        ]
        return json_safe({
            "schema_version": "1.0",
            "run_id": self.run_id,
            "status": self.status.value,
            "answer": self.final_answer,
            "insights": self.insights,
            "table": self.table,
            "charts": self.chart_configs,
            "sql": {
                "text": self.generated_sql,
                "dialect": self.scope.get("sql_dialect", ""),
                "duration_ms": duration_ms,
                "validation": self.validation_result,
            },
            "scope": self.scope,
            "warnings": self.warnings,
            "suggested_questions": self.suggested_questions,
            "agent_trace": agent_trace,
            "error": self.error,
        })


@dataclass
class PipelineResult(AgentState):
    """向后兼容的 Pipeline 输出类型。"""
