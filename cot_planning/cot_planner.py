from __future__ import annotations

import re
from typing import Iterator, List, Optional

from .objects import CotPlanningRequest, CotPlanningResult, CotPlanningStep, FewShotExample
from .prompt_builder import CotPromptBuilder
from .thinking_client import ThinkingModelClient


class CotPlanner:
    """
    CoT 规划服务。

    该服务位于 Schema 检索与 SQL 生成之间。
    """

    def __init__(
        self,
        prompt_builder: Optional[CotPromptBuilder] = None,
        thinking_client: Optional[ThinkingModelClient] = None,
    ):
        self.prompt_builder = prompt_builder or CotPromptBuilder()
        self.thinking_client = thinking_client or ThinkingModelClient()

    def plan(
        self,
        user_query: str,
        schema_graph: object,
        output_format_spec: str = "",
        few_shots: Optional[List[FewShotExample]] = None,
    ) -> CotPlanningResult:
        """
        非流式生成 CoT 规划结果。
        """
        schema_context = self._schema_to_text(schema_graph)

        request = CotPlanningRequest(
            user_query=user_query,
            schema_context=schema_context,
            output_format_spec=output_format_spec,
            few_shots=few_shots or [],
        )

        prompt = self.prompt_builder.build(request)
        raw_output = self.thinking_client.generate(prompt)
        steps = self.parse_steps(raw_output)

        return CotPlanningResult(
            user_query=user_query,
            prompt=prompt,
            raw_output=raw_output,
            steps=steps,
        )

    def plan_stream(
        self,
        user_query: str,
        schema_graph: object,
        output_format_spec: str = "",
        few_shots: Optional[List[FewShotExample]] = None,
    ) -> Iterator[str]:
        """
        流式生成 CoT 规划结果。

        该方法只负责边生成边返回文本片段。
        如果需要解析最终 steps，可以在外部收集完整输出后调用 parse_steps()。
        """
        schema_context = self._schema_to_text(schema_graph)

        request = CotPlanningRequest(
            user_query=user_query,
            schema_context=schema_context,
            output_format_spec=output_format_spec,
            few_shots=few_shots or [],
        )

        prompt = self.prompt_builder.build(request)

        yield from self.thinking_client.generate_stream(prompt)

    def parse_steps(self, text: str) -> List[CotPlanningStep]:
        """
        解析模型输出中的步骤四元组。

        解析失败时不影响主流程，直接返回空列表。
        """
        pattern = re.compile(
            r"步骤(?P<step_no>\d+)：\s*"
            r"\(\s*"
            r"数据库:\s*(?P<database>.*?)\s*,\s*"
            r"处理对象:\s*(?P<processing_objects>.*?)\s*,\s*"
            r"操作指令:\s*(?P<operation_instruction>.*?)\s*,\s*"
            r"输出目标:\s*(?P<output_target>.*?)\s*"
            r"\)",
            re.S,
        )

        steps: List[CotPlanningStep] = []

        for match in pattern.finditer(text):
            steps.append(
                CotPlanningStep(
                    step_no=int(match.group("step_no")),
                    database=self._clean(match.group("database")),
                    processing_objects=self._clean(match.group("processing_objects")),
                    operation_instruction=self._clean(match.group("operation_instruction")),
                    output_target=self._clean(match.group("output_target")),
                )
            )

        return steps

    def _schema_to_text(self, schema_graph: object) -> str:
        """
        将 SchemaGraph 转换为文本。
        """
        if isinstance(schema_graph, str):
            return schema_graph

        if hasattr(schema_graph, "to_prompt_context"):
            return schema_graph.to_prompt_context()

        raise TypeError(
            "schema_graph 必须是字符串，或带有 to_prompt_context() 方法的对象。"
        )

    def _clean(self, value: str) -> str:
        """
        清理解析结果。
        """
        return value.strip().rstrip(",").strip()
