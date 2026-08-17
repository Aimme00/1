from __future__ import annotations

from pathlib import Path
import re
from typing import Callable, Dict, List, Optional

from chart_generation import EChartsRecommender
from cot_planning import CotPlanner, ThinkingModelClient, ThinkingModelConfig
from data_analysis import AnalysisResult, DeterministicDataAnalyzer
from mcp_router import MCPRouter, QueryExecutor, SQLiteMCPExecutor
from response_generation import GroundedResponseGenerator
from result_quality import ResultQualityValidator
from schema_retrieval.hybrid_schema_retrieval_service import (
    HybridSchemaRetrievalConfig,
    HybridSchemaRetrievalService,
)
from schema_retrieval.rerank_client import AliyunRerankClient, AliyunRerankConfig
from schema_retrieval.rrf_fusion_client import RRFFusionConfig
from sql_generation import (
    CoderModelClient,
    CoderModelConfig,
    CotStep,
    LocalSchemaStore,
    SqlGenerator,
    normalize_sql_for_dialect,
)
from sql_validation import (
    SQLValidationRepairLoop,
    SQLValidator,
    SQLValidatorConfig,
)
from model_provider import allow_mock_model

from .demo_data import create_trade_demo_database, get_trade_business_meta
from .drilldown import build_drill_actions
from .local_clients import LocalHashEmbeddingClient, SimpleKeywordExtractor
from .objects import AgentRunStatus, PipelineConfig, PipelineResult, StepExecutionLog
from .verified_demo_questions import find_verified_demo_question


SqlGeneratorFactory = Callable[[LocalSchemaStore], SqlGenerator]
EventCallback = Callable[[str, str, str, Dict[str, object]], None]


class AskDataText2SQLPipeline:
    """
    AskData Text2SQL 端到端流程。

    当前实现串联：
    1. 初始化已注入的数据源，或在兼容模式创建测试数据库
    2. Schema 检索与 SchemaGraph 构建
    3. CoT 四元组规划
    4. SQL 生成
    5. SQL 校验与有限次数自动修正
    6. MCP 路由执行

    数据结果合理性校验、分析和图表生成将在后续阶段补充。
    """

    def __init__(
        self,
        config: Optional[PipelineConfig] = None,
        *,
        schema_retrieval_service: Optional[HybridSchemaRetrievalService] = None,
        cot_planner: Optional[CotPlanner] = None,
        mcp_router: Optional[MCPRouter] = None,
        query_executor: Optional[QueryExecutor] = None,
        keyword_extractor: Optional[object] = None,
        sql_validator: Optional[SQLValidator] = None,
        sql_generator_factory: Optional[SqlGeneratorFactory] = None,
        business_meta: Optional[Dict[str, object]] = None,
        result_quality_validator: Optional[ResultQualityValidator] = None,
        data_analyzer: Optional[DeterministicDataAnalyzer] = None,
        chart_recommender: Optional[EChartsRecommender] = None,
        response_generator: Optional[GroundedResponseGenerator] = None,
    ):
        self.config = config or PipelineConfig()
        self.keyword_extractor = keyword_extractor or SimpleKeywordExtractor()
        self.db_path = Path(self.config.db_path)
        self.business_meta = business_meta

        if schema_retrieval_service is None:
            if self.config.database_type != "sqlite":
                raise ValueError(
                    "非 SQLite 数据源必须注入 schema_retrieval_service。"
                )
            if self.config.bootstrap_demo_database:
                self.db_path = create_trade_demo_database(self.db_path)
                self.business_meta = self.business_meta or get_trade_business_meta()
            elif not self.db_path.exists():
                raise FileNotFoundError(f"数据库文件不存在：{self.db_path}")
            self.schema_retrieval_service = self._build_schema_retrieval_service()
        else:
            self.schema_retrieval_service = schema_retrieval_service

        self.cot_planner = cot_planner or self._build_cot_planner()
        self.mcp_router = mcp_router or self._build_mcp_router(query_executor)
        self.sql_validator = sql_validator or SQLValidator(
            SQLValidatorConfig(
                dialect=self.config.sql_dialect,
                max_rows=self.config.max_query_rows,
                require_sqlglot=self.config.require_sqlglot,
            )
        )
        self.sql_generator_factory = sql_generator_factory or self._default_sql_generator
        self.result_quality_validator = result_quality_validator or ResultQualityValidator()
        self.data_analyzer = data_analyzer or DeterministicDataAnalyzer()
        self.chart_recommender = chart_recommender or EChartsRecommender()
        self.response_generator = response_generator or GroundedResponseGenerator()

    def run(
        self,
        query: str,
        keywords: Optional[List[str]] = None,
        conversation_context: str = "",
        *,
        run_id: Optional[str] = None,
        user_id: str = "",
        session_id: str = "",
        event_callback: Optional[EventCallback] = None,
        should_cancel: Optional[Callable[[], bool]] = None,
        generate_chart: Optional[bool] = None,
    ) -> PipelineResult:
        """
        执行完整 Text2SQL 流程。
        """
        state = PipelineResult(
            query=query,
            user_id=user_id,
            session_id=session_id,
            status=AgentRunStatus.RUNNING,
        )
        if run_id:
            state.run_id = run_id

        verified_demo = find_verified_demo_question(query)
        if verified_demo:
            state.scope["verified_demo"] = True
            state.scope["verified_demo_question"] = verified_demo.question

        try:
            self._emit(event_callback, "run", "running", "Agent 开始处理问题")
            if self._cancelled(state, should_cancel, event_callback):
                return state
            unsafe_intent = self._unsafe_user_intent(query)
            if unsafe_intent:
                return self._reject_unsupported(
                    state,
                    event_callback,
                    f"当前 Agent 仅支持只读数据分析，不能执行{unsafe_intent}操作。",
                )
            resolved_keywords = keywords or self.keyword_extractor.extract(query)
            state.keywords = resolved_keywords

            contextual_query = query
            if conversation_context.strip():
                contextual_query = (
                    f"以下是与当前问题相关的会话记忆：\n{conversation_context}\n\n"
                    f"当前用户问题：{query}"
                )

            self._emit(event_callback, "schema", "running", "正在检索相关 Schema")
            retrieval_result = self.schema_retrieval_service.retrieve(
                query=contextual_query,
                keywords=resolved_keywords,
            )

            schema_graph = retrieval_result.schema_graph
            state.schema_context = schema_graph.to_prompt_context()
            state.scope["field_labels"] = {
                column.column_name: (
                    column.aliases[0]
                    if column.aliases
                    else (column.description.rstrip("。") or column.column_name)
                )
                for columns in schema_graph.columns.values()
                for column in columns
            }
            self._emit(
                event_callback,
                "schema",
                "completed",
                "Schema 检索完成",
                {
                    "keywords": resolved_keywords,
                    "tables": list(schema_graph.tables.keys()),
                    "columns": [
                        column.column_name
                        for columns in schema_graph.columns.values()
                        for column in columns
                    ],
                },
            )
            if self._cancelled(state, should_cancel, event_callback):
                return state

            self._emit(event_callback, "plan", "running", "正在拆解分析步骤")
            if verified_demo:
                cot_result = verified_demo.build_plan(self.config.database_name)
            else:
                cot_result = self.cot_planner.plan(
                    user_query=contextual_query,
                    schema_graph=schema_graph,
                )
            state.cot_output = cot_result.raw_output
            state.plan = [
                {
                    "database": step.database,
                    "processing_objects": step.processing_objects,
                    "operation_instruction": step.operation_instruction,
                    "output_target": step.output_target,
                }
                for step in cot_result.steps
            ]
            unsupported_reason = self._unsupported_plan_reason(cot_result.steps)
            if unsupported_reason:
                return self._reject_unsupported(
                    state,
                    event_callback,
                    unsupported_reason,
                    node="plan",
                )
            self._emit(
                event_callback,
                "plan",
                "completed",
                f"已生成 {len(cot_result.steps)} 个执行步骤",
                {
                    "steps": [
                        {
                            "step": index,
                            "operation": step.operation_instruction,
                            "output": step.output_target,
                        }
                        for index, step in enumerate(cot_result.steps, start=1)
                    ]
                },
            )

            schema_store = LocalSchemaStore.from_schema_graph(schema_graph)
            sql_generator = self.sql_generator_factory(schema_store)
            repair_loop = SQLValidationRepairLoop(
                validator=self.sql_validator,
                max_repair_attempts=self.config.max_sql_repair_attempts,
            )

            for step_index, cot_step in enumerate(cot_result.steps, start=1):
                if self._cancelled(state, should_cancel, event_callback):
                    return state
                sql_cot_step = CotStep(
                    database=cot_step.database,
                    processing_objects=cot_step.processing_objects,
                    operation_instruction=cot_step.operation_instruction,
                    output_target=cot_step.output_target,
                )

                local_schema = schema_store.extract_local_schema(sql_cot_step)
                self._emit(event_callback, "sql_generate", "running", f"正在生成第 {step_index} 段 SQL")
                if verified_demo:
                    initial_sql = verified_demo.sql_for(self.config.sql_dialect)
                else:
                    generation_result = sql_generator.generate(
                        sql_cot_step,
                        sql_dialect=self.config.sql_dialect,
                    )
                    initial_sql = normalize_sql_for_dialect(
                        generation_result.sql,
                        self.config.sql_dialect,
                    )
                self._emit(
                    event_callback,
                    "sql_generate",
                    "completed",
                    f"第 {step_index} 段 SQL 已生成",
                    {
                        "sql": initial_sql,
                        "dialect": self.config.sql_dialect,
                    },
                )

                def repair_sql(
                    previous_sql: str,
                    feedback: str,
                    attempt_number: int,
                ) -> str:
                    if verified_demo:
                        return verified_demo.sql_for(self.config.sql_dialect)
                    correction = (
                        f"第 {attempt_number} 次 SQL：{previous_sql}\n"
                        f"校验错误：{feedback}"
                    )
                    repaired_sql = sql_generator.generate(
                        sql_cot_step,
                        sql_dialect=self.config.sql_dialect,
                        correction_context=correction,
                    ).sql
                    return normalize_sql_for_dialect(
                        repaired_sql,
                        self.config.sql_dialect,
                    )

                allowed_columns = {
                    table_name: [column.column_name for column in table.columns]
                    for table_name, table in local_schema.tables.items()
                }
                outcome = repair_loop.run(
                    initial_sql,
                    repair=repair_sql,
                    allowed_tables=local_schema.tables.keys(),
                    allowed_columns=allowed_columns,
                )
                state.generated_sql = outcome.sql
                state.validation_result = outcome.validation.to_dict()
                state.warnings.extend(
                    issue.message
                    for issue in outcome.validation.issues
                    if issue.level.value == "warning"
                    and issue.message not in state.warnings
                )
                self._emit(
                    event_callback,
                    "sql_validate",
                    "completed" if outcome.success else "failed",
                    "SQL 安全校验通过" if outcome.success else "SQL 安全校验失败",
                    {
                        "valid": outcome.validation.is_valid,
                        "parser": outcome.validation.parser,
                        "tables": sorted(outcome.validation.tables),
                        "attempt_count": len(outcome.attempts),
                        "issues": [issue.to_dict() for issue in outcome.validation.issues],
                    },
                )

                if not outcome.success:
                    error_message = outcome.validation.feedback_text()
                    failed_result = {
                        "database": sql_cot_step.database,
                        "sql": outcome.sql,
                        "success": False,
                        "columns": [],
                        "rows": [],
                        "row_count": 0,
                        "truncated": False,
                        "duration_ms": 0,
                        "error": error_message,
                    }
                    state.step_logs.append(
                        StepExecutionLog(
                            database=sql_cot_step.database,
                            cot_step=sql_cot_step,
                            local_schema=local_schema.to_prompt_context(),
                            sql=outcome.sql,
                            validation_result=outcome.validation.to_dict(),
                            sql_attempts=[attempt.to_dict() for attempt in outcome.attempts],
                            execution_request={},
                            execution_result=failed_result,
                        )
                    )
                    state.query_result = failed_result
                    state.error = {
                        "code": "sql_validation_failed",
                        "message": error_message,
                    }
                    state.final_answer = (
                        "生成的 SQL 未通过安全校验，请补充查询条件或调整问题后重试。"
                    )
                    state.status = AgentRunStatus.FAILED
                    break

                execution_request = {
                    "database": sql_cot_step.database,
                    "sql": outcome.sql,
                }
                if self._cancelled(state, should_cancel, event_callback):
                    return state
                self._emit(event_callback, "sql_execute", "running", "正在执行只读 SQL 查询")
                execution_result = self.mcp_router.execute(execution_request)
                execution_repair_attempts: List[Dict[str, object]] = []
                execution_attempt_limit = (
                    0 if verified_demo else self.config.max_execution_repair_attempts
                )
                for execution_attempt in range(1, execution_attempt_limit + 1):
                    if execution_result.success:
                        break
                    database_error = self._safe_database_error(execution_result.error)
                    self._emit(
                        event_callback,
                        "sql_generate",
                        "running",
                        f"数据库执行报错，正在进行第 {execution_attempt} 次方言修正",
                    )
                    correction = (
                        f"上一次 SQL：{execution_result.sql or outcome.sql}\n"
                        f"目标数据库方言：{self.config.sql_dialect}\n"
                        f"数据库执行错误：{database_error}\n"
                        "请修复方言、函数类型或聚合语义错误，保持原查询意图。"
                    )
                    repaired_sql = normalize_sql_for_dialect(
                        sql_generator.generate(
                            sql_cot_step,
                            sql_dialect=self.config.sql_dialect,
                            correction_context=correction,
                        ).sql,
                        self.config.sql_dialect,
                    )
                    repaired_validation = self.sql_validator.validate(
                        repaired_sql,
                        allowed_tables=local_schema.tables.keys(),
                        allowed_columns=allowed_columns,
                    )
                    execution_repair_attempts.append({
                        "number": execution_attempt,
                        "trigger": "execution_error",
                        "database_error": database_error,
                        "sql": repaired_sql,
                        "validation": repaired_validation.to_dict(),
                    })
                    if not repaired_validation.is_valid:
                        execution_result = type(execution_result)(
                            database=sql_cot_step.database,
                            sql=repaired_sql,
                            success=False,
                            error=repaired_validation.feedback_text(),
                        )
                        continue
                    state.generated_sql = repaired_validation.validated_sql
                    state.validation_result = repaired_validation.to_dict()
                    execution_request = {
                        "database": sql_cot_step.database,
                        "sql": repaired_validation.validated_sql,
                    }
                    execution_result = self.mcp_router.execute(execution_request)
                if verified_demo and execution_result.success:
                    contract_error = verified_demo.result_contract_error(
                        execution_result.columns,
                        execution_result.row_count,
                    )
                    if contract_error:
                        execution_result = type(execution_result)(
                            database=execution_result.database,
                            sql=execution_result.sql,
                            success=False,
                            columns=execution_result.columns,
                            rows=execution_result.rows,
                            row_count=execution_result.row_count,
                            truncated=execution_result.truncated,
                            duration_ms=execution_result.duration_ms,
                            error=contract_error,
                        )
                execution_payload = execution_result.to_dict()
                state.query_result = execution_payload
                self._emit(
                    event_callback,
                    "sql_execute",
                    "completed" if execution_result.success else "failed",
                    f"查询完成，返回 {execution_payload.get('row_count', 0)} 行" if execution_result.success else "数据库查询失败",
                    {
                        "database": execution_payload.get("database", sql_cot_step.database),
                        "row_count": execution_payload.get("row_count", 0),
                        "duration_ms": execution_payload.get("duration_ms", 0),
                        "columns": execution_payload.get("columns") or [],
                        "truncated": bool(execution_payload.get("truncated", False)),
                    },
                )

                state.step_logs.append(
                    StepExecutionLog(
                        database=sql_cot_step.database,
                        cot_step=sql_cot_step,
                        local_schema=local_schema.to_prompt_context(),
                        sql=execution_result.sql or state.generated_sql or outcome.sql,
                        validation_result=state.validation_result,
                        sql_attempts=(
                            [attempt.to_dict() for attempt in outcome.attempts]
                            + execution_repair_attempts
                        ),
                        execution_request=execution_request,
                        execution_result=execution_payload,
                    )
                )

                if not execution_result.success:
                    state.error = {
                        "code": "query_execution_failed",
                        "message": execution_result.error or "数据库执行失败",
                    }
                    state.final_answer = "数据库查询未成功，请稍后重试或缩小查询范围。"
                    state.status = AgentRunStatus.FAILED
                    break

            if state.status == AgentRunStatus.RUNNING:
                if self._cancelled(state, should_cancel, event_callback):
                    return state
                chart_requested = self.should_generate_chart(query, generate_chart)
                self._emit(
                    event_callback,
                    "analysis",
                    "running",
                    "正在分析数据并生成图表"
                    if chart_requested
                    else "正在分析数据并生成结论",
                )
                self._enrich_state(state, generate_chart=generate_chart)
                self._emit(
                    event_callback,
                    "analysis",
                    "completed",
                    "数据分析与图表生成完成"
                    if chart_requested
                    else "数据分析与结论生成完成",
                    {
                        "answer": state.final_answer,
                        "insight_count": len(state.insights),
                        "chart_count": len(state.chart_configs),
                    },
                )
            if state.status == AgentRunStatus.RUNNING:
                state.status = AgentRunStatus.COMPLETED
                self._emit(event_callback, "run", "completed", "Agent 运行完成")
        except Exception as exc:
            state.status = AgentRunStatus.FAILED
            state.error = {
                "code": "pipeline_error",
                "message": str(exc),
            }
            state.final_answer = "处理问题时发生错误，请稍后重试。"
            self._emit(event_callback, "run", "failed", state.error["message"])

        return state

    @staticmethod
    def _safe_database_error(error: Optional[str]) -> str:
        """只把可用于 SQL 修复的错误文本发给模型，避免过长日志或连接信息外泄。"""
        message = re.sub(r"postgres(?:ql)?://\S+", "[database-url-redacted]", str(error or ""), flags=re.I)
        return message[:2000] or "未知数据库执行错误"

    @staticmethod
    def _unsafe_user_intent(query: str) -> str:
        """在进入模型前拦截写入、破坏和个人敏感信息诉求。"""
        normalized = "".join(query.lower().split())
        patterns = (
            (r"(?:删除|删掉|清空|移除).*(?:订单|数据|记录|表)", "删除"),
            (r"(?:修改|更新|改掉).*(?:订单|数据|记录|字段)", "修改"),
            (r"(?:新增|插入|写入|导入).*(?:订单|数据|记录)", "写入"),
            (
                r"(?:手机号|手机号码|电话号码|身份证号|身份证号码|邮箱地址|电子邮箱|家庭住址|详细地址)",
                "个人敏感信息查询",
            ),
        )
        for pattern, label in patterns:
            if re.search(pattern, normalized):
                return label
        return ""

    @staticmethod
    def _unsupported_plan_reason(steps: List[object]) -> str:
        """规划无法被当前 Schema 支撑时 fail closed，禁止继续生成兜底 SQL。"""
        if not steps:
            return "当前数据无法形成可靠的查询计划，请换一种问法或补充指标与筛选条件。"
        for step in steps:
            database = str(getattr(step, "database", ""))
            planning_text = " ".join(
                str(getattr(step, name, ""))
                for name in ("processing_objects", "operation_instruction", "output_target")
            )
            if database in {"", "缺失"} or any(
                marker in planning_text
                for marker in ("Schema中未找到", "无法完整支撑", "无法生成明确输出目标")
            ):
                return "当前数据库缺少回答该问题所需的字段或关联关系，请更换问题或联系管理员补充数据。"
        return ""

    @staticmethod
    def _reject_unsupported(
        state: PipelineResult,
        event_callback: Optional[EventCallback],
        message: str,
        *,
        node: str = "intent",
    ) -> PipelineResult:
        state.status = AgentRunStatus.FAILED
        state.error = {"code": "unsupported_query", "message": message}
        state.final_answer = message
        AskDataText2SQLPipeline._emit(event_callback, node, "failed", message)
        AskDataText2SQLPipeline._emit(event_callback, "run", "failed", message)
        return state

    @staticmethod
    def _emit(
        callback: Optional[EventCallback],
        node: str,
        status: str,
        message: str,
        data: Optional[Dict[str, object]] = None,
    ) -> None:
        if callback:
            callback(node, status, message, data or {})

    def _cancelled(
        self,
        state: PipelineResult,
        should_cancel: Optional[Callable[[], bool]],
        event_callback: Optional[EventCallback],
    ) -> bool:
        if not should_cancel or not should_cancel():
            return False
        state.status = AgentRunStatus.CANCELLED
        state.final_answer = "运行已取消。"
        self._emit(event_callback, "run", "cancelled", "运行已取消")
        return True

    def _enrich_state(
        self,
        state: PipelineResult,
        *,
        generate_chart: Optional[bool] = None,
    ) -> None:
        """质量检查 → 确定性分析 → 图表 → 可信回答。"""
        quality = self.result_quality_validator.validate(state.query_result)
        state.quality_result = quality.to_dict()
        for warning in quality.warnings:
            if warning not in state.warnings:
                state.warnings.append(warning)

        field_labels = state.scope.get("field_labels", {})
        state.table = {
            "columns": state.query_result.get("columns") or [],
            "column_meta": [
                {
                    "key": column,
                    "label": field_labels.get(column, column),
                }
                for column in (state.query_result.get("columns") or [])
            ],
            "rows": state.query_result.get("rows") or [],
            "returned_rows": quality.row_count,
            "total_rows": None if quality.truncated else quality.row_count,
            "truncated": quality.truncated,
        }
        state.scope.update({
            "database": self.config.database_name,
            "sql_dialect": self.config.sql_dialect,
            "row_count": quality.row_count,
            "truncated": quality.truncated,
        })

        analysis = AnalysisResult(row_count=quality.row_count)
        if quality.usable:
            try:
                analysis = self.data_analyzer.analyze(
                    state.query,
                    state.query_result,
                    field_labels=field_labels,
                )
            except Exception as exc:
                state.warnings.append(f"数据分析节点降级：{exc}")
        state.analysis_result = analysis.to_dict()
        state.insights = [insight.to_dict() for insight in analysis.insights]

        chart_requested = self.should_generate_chart(state.query, generate_chart)
        state.scope["chart_requested"] = chart_requested
        if chart_requested:
            try:
                charts = self.chart_recommender.recommend(
                    state.query,
                    state.query_result,
                    analysis,
                )
                state.chart_configs = [chart.to_dict() for chart in charts]
            except Exception as exc:
                state.warnings.append(f"图表节点降级：{exc}")
                state.chart_configs = []
        else:
            state.chart_configs = []

        try:
            generated = self.response_generator.generate(
                query=state.query,
                result=state.query_result,
                quality=quality,
                analysis=analysis,
                database=self.config.database_name,
            )
            state.final_answer = generated.answer
            state.suggested_questions = generated.suggested_questions
            state.scope.update(generated.scope)
        except Exception as exc:
            state.warnings.append(f"回答节点降级：{exc}")
            state.final_answer = f"查询成功，共返回 {quality.row_count} 条记录。"

        state.scope["drill_actions"] = build_drill_actions(
            columns=state.table.get("columns") or [],
            rows=state.table.get("rows") or [],
        )

        if not quality.usable:
            state.error = {
                "code": "result_quality_failed",
                "message": "查询结果未通过质量检查。",
            }
            state.status = AgentRunStatus.FAILED

    def should_generate_chart(
        self,
        query: str,
        explicit_choice: Optional[bool] = None,
    ) -> bool:
        """图表默认关闭；显式选择或自然语言明确要求时才开启。"""
        if explicit_choice is not None:
            return explicit_choice
        if self.config.auto_generate_charts:
            return True
        normalized = "".join(query.lower().split())
        chart_terms = (
            "生成图表",
            "生成图片",
            "画个图",
            "画一个图",
            "可视化",
            "折线图",
            "柱状图",
            "条形图",
            "饼图",
            "环形图",
            "散点图",
            "趋势图",
        )
        if any(term in normalized for term in chart_terms):
            return True
        action_requested = any(term in normalized for term in ("生成", "绘制", "画出", "画成"))
        visual_requested = any(term in normalized for term in ("图表", "图片", "图像"))
        return action_requested and visual_requested

    def _build_schema_retrieval_service(self) -> HybridSchemaRetrievalService:
        """
        构建 Schema 检索服务。
        """
        embedding_client = LocalHashEmbeddingClient(dimensions=1024)

        rerank_client = AliyunRerankClient(
            AliyunRerankConfig(
                api_key="",
                workspace_id="",
                model="qwen-rerank",
            )
        )

        return HybridSchemaRetrievalService.from_sqlite(
            db_path=self.db_path,
            database_name=self.config.database_name,
            business_meta=self.business_meta or {},
            embedding_client=embedding_client,
            rerank_client=rerank_client,
            keyword_extractor=None,
            config=HybridSchemaRetrievalConfig(
                per_keyword_top_k=20,
                include_join_columns=True,
                rrf_config=RRFFusionConfig(
                    rrf_k=60,
                    truncate_multiplier=6,
                    min_fused_top_k=10,
                    max_fused_top_k=50,
                    final_top_k=20,
                    route_weights={
                        "keyword": 1.0,
                        "vector": 1.0,
                    },
                ),
                rerank_top_multiplier=6,
                rerank_min_top_n=12,
                rerank_max_top_n=20,
            ),
            sample_size=self.config.sample_size,
        )

    def _build_cot_planner(self) -> CotPlanner:
        """
        构建 CoT 规划器。
        """
        return CotPlanner(
            thinking_client=ThinkingModelClient(
                ThinkingModelConfig(
                    use_mock_when_no_api_key=allow_mock_model(),
                    temperature=0.0,
                )
            )
        )

    def _build_mcp_router(
        self,
        query_executor: Optional[QueryExecutor] = None,
    ) -> MCPRouter:
        """
        构建 MCP 路由器。
        """
        router = MCPRouter()

        if query_executor is not None:
            router.register_executor(
                database=self.config.database_name,
                executor=query_executor,
            )
            return router

        if self.config.database_type != "sqlite":
            raise ValueError(
                "非 SQLite 数据源必须注入 query_executor 或 mcp_router。"
            )

        router.register_executor(
            database=self.config.database_name,
            executor=SQLiteMCPExecutor(
                database=self.config.database_name,
                db_path=self.db_path,
                readonly=True,
                max_rows=self.config.max_query_rows,
            ),
        )

        return router

    @staticmethod
    def _default_sql_generator(schema_store: LocalSchemaStore) -> SqlGenerator:
        return SqlGenerator(
            schema_store=schema_store,
            coder_client=CoderModelClient(
                CoderModelConfig(use_mock_when_no_api_key=allow_mock_model())
            ),
        )
