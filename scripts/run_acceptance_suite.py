#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from askdata_pipeline import AgentRunStatus, AskDataText2SQLPipeline, PipelineConfig
from backend.data_source import DataSourceManager, DataSourceSettings
from env_settings import env_text
from model_call_budget import ModelCallLimitExceeded, model_call_budget
from model_provider import has_model_api_key


FIXTURE = PROJECT_ROOT / "tests" / "fixtures" / "acceptance_questions.json"
DEFAULT_REPORT = PROJECT_ROOT / "runtime_data" / "acceptance-report.json"
REAL_CALL_LIMIT = 20


def _load_local_env() -> None:
    """Load simple local .env values without printing or overwriting secrets."""
    path = PROJECT_ROOT / ".env"
    if not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ and value:
            os.environ[key] = value


def _load_questions() -> list[dict[str, Any]]:
    questions = json.loads(FIXTURE.read_text(encoding="utf-8"))
    if len(questions) != 20 or len({item["id"] for item in questions}) != 20:
        raise RuntimeError("验收集必须恰好包含 20 个不重复问题")
    return questions


def _result_check(case: dict[str, Any], result) -> tuple[bool, str]:
    expected = AgentRunStatus(case["expect"])
    if result.status != expected:
        return False, f"状态应为 {expected.value}，实际为 {result.status.value}：{result.error}"
    if expected == AgentRunStatus.FAILED:
        expected_code = case.get("error_code")
        actual_code = (result.error or {}).get("code")
        if expected_code and actual_code != expected_code:
            return False, f"错误码应为 {expected_code}，实际为 {actual_code}"
        return True, "安全拒绝符合预期"
    if not result.query_result.get("success"):
        return False, f"查询未成功：{result.error}"
    columns = {str(item).lower() for item in result.query_result.get("columns") or []}
    for expected_column in case.get("columns_any") or []:
        normalized = expected_column.lower()
        if not any(normalized == column or normalized in column for column in columns):
            return False, f"缺少预期字段 {expected_column}；实际字段：{sorted(columns)}"
    if case.get("chart") and not result.chart_configs:
        return False, "明确要求图表，但结果没有图表配置"
    return True, f"返回 {result.query_result.get('row_count', 0)} 行"


def _run_cases(pipeline, cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    outcomes: list[dict[str, Any]] = []
    for case in cases:
        result = pipeline.run(
            case["question"],
            generate_chart=case.get("chart"),
        )
        error_message = str((result.error or {}).get("message") or "")
        if "真实模型调用预算已用完" in error_message:
            # Pipeline 会把节点异常包装为 pipeline_error。这里重新抛出预算异常，
            # 立即停止余下问题，避免反复尝试一批注定会在联网前被拦截的调用。
            raise ModelCallLimitExceeded(error_message)
        passed, detail = _result_check(case, result)
        outcomes.append(
            {
                "id": case["id"],
                "mode": case["mode"],
                "category": case["category"],
                "question": case["question"],
                "passed": passed,
                "detail": detail,
                "status": result.status.value,
                "error_code": (result.error or {}).get("code"),
                "columns": result.query_result.get("columns") or [],
                "row_count": result.query_result.get("row_count", 0),
                "chart_count": len(result.chart_configs),
            }
        )
    return outcomes


def _deterministic_pipeline(temp_dir: str) -> AskDataText2SQLPipeline:
    return AskDataText2SQLPipeline(
        PipelineConfig(
            db_path=Path(temp_dir) / "acceptance.db",
            max_sql_repair_attempts=0,
            max_execution_repair_attempts=0,
            require_sqlglot=False,
        )
    )


def _real_pipeline(temp_dir: str) -> AskDataText2SQLPipeline:
    database_type = env_text("ASKDATA_DATABASE_TYPE").lower()
    if database_type in {"postgres", "postgresql", "neon"}:
        manager = DataSourceManager(
            Path(temp_dir) / "runtime",
            DataSourceSettings.from_environment(),
        )
        pipeline = manager.get_pipeline()
    else:
        pipeline = AskDataText2SQLPipeline(
            PipelineConfig(
                db_path=Path(temp_dir) / "real-model-acceptance.db",
                require_sqlglot=False,
            )
        )
    pipeline.config = replace(
        pipeline.config,
        max_sql_repair_attempts=0,
        max_execution_repair_attempts=0,
    )
    return pipeline


def main() -> int:
    parser = argparse.ArgumentParser(description="运行问数 20 题验收集")
    parser.add_argument(
        "--real",
        action="store_true",
        help="执行其中 10 道真实模型题；总模型 HTTP 请求硬限制为 20 次",
    )
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args()

    _load_local_env()
    questions = _load_questions()
    deterministic = [item for item in questions if item["mode"] == "deterministic"]
    real = [item for item in questions if item["mode"] == "real"]
    report: dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "question_count": len(questions),
        "real_model_call_limit": REAL_CALL_LIMIT,
        "real_model_calls": 0,
        "real_suite_requested": args.real,
        "real_suite_blocked_reason": "",
        "outcomes": [],
    }

    with tempfile.TemporaryDirectory() as temp_dir:
        with model_call_budget(0) as deterministic_budget:
            report["outcomes"].extend(
                _run_cases(_deterministic_pipeline(temp_dir), deterministic)
            )
        if deterministic_budget.calls != 0:
            raise AssertionError("确定性验收不应消耗真实模型调用")

        if args.real:
            if not has_model_api_key():
                report["real_suite_blocked_reason"] = (
                    "当前进程没有可用的 DeepSeek/DashScope API Key；未发送任何真实请求"
                )
            else:
                try:
                    with model_call_budget(REAL_CALL_LIMIT) as budget:
                        report["outcomes"].extend(
                            _run_cases(_real_pipeline(temp_dir), real)
                        )
                    report["real_model_calls"] = budget.calls
                    report["model_call_records"] = [
                        {
                            "number": item.number,
                            "role": item.role,
                            "provider": item.provider,
                            "model": item.model,
                        }
                        for item in budget.records
                    ]
                except ModelCallLimitExceeded as exc:
                    report["real_model_calls"] = budget.calls
                    report["model_call_records"] = [
                        {
                            "number": item.number,
                            "role": item.role,
                            "provider": item.provider,
                            "model": item.model,
                        }
                        for item in budget.records
                    ]
                    report["real_suite_blocked_reason"] = str(exc)

    outcomes = report["outcomes"]
    report["passed"] = sum(bool(item["passed"]) for item in outcomes)
    report["failed"] = sum(not bool(item["passed"]) for item in outcomes)
    report["executed"] = len(outcomes)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(
        f"验收完成：执行 {report['executed']}/20，"
        f"通过 {report['passed']}，失败 {report['failed']}，"
        f"真实模型调用 {report['real_model_calls']}/{REAL_CALL_LIMIT}"
    )
    if report["real_suite_blocked_reason"]:
        print(report["real_suite_blocked_reason"])
    print(f"报告：{args.report}")
    return 1 if report["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
