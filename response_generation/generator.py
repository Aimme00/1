from __future__ import annotations

from typing import Any, Dict

from data_analysis import AnalysisResult
from result_quality import ResultQualityReport

from .objects import GeneratedResponse


class GroundedResponseGenerator:
    """只使用查询结果与确定性分析生成回答，避免模型凭空补充数字。"""

    def generate(
        self,
        *,
        query: str,
        result: Dict[str, Any],
        quality: ResultQualityReport,
        analysis: AnalysisResult,
        database: str,
    ) -> GeneratedResponse:
        if not quality.usable:
            answer = "查询结果未通过质量检查，暂时无法生成可靠结论。"
        elif quality.empty:
            answer = "当前查询条件下没有数据，建议检查时间范围、筛选条件或数据口径。"
        elif analysis.insights:
            answer = " ".join(insight.text for insight in analysis.insights[:3])
        else:
            answer = f"查询成功，共返回 {quality.row_count} 条记录。"

        suggestions = self._suggest_questions(analysis)
        return GeneratedResponse(
            answer=answer,
            suggested_questions=suggestions,
            scope={
                "database": database,
                "row_count": quality.row_count,
                "truncated": quality.truncated,
                "question": query,
            },
        )

    @staticmethod
    def _suggest_questions(analysis: AnalysisResult) -> list[str]:
        suggestions: list[str] = []
        profile_kinds = {profile.kind for profile in analysis.field_profiles}
        if "temporal" not in profile_kinds:
            suggestions.append("可以按时间维度查看趋势吗？")
        if "categorical" in profile_kinds and analysis.primary_metric:
            suggestions.append("按主要维度展示排名前10。")
        if analysis.row_count:
            suggestions.append("导出当前查询结果。")
        return suggestions[:3]
