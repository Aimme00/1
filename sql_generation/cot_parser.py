from __future__ import annotations

import re
from typing import List

from .objects import CotStep


class CotStepParser:
    """
    CoT 四元组解析器。

    使用正则表达式从 CoT 输出中提取：
    - 数据库
    - 处理对象
    - 操作指令
    - 输出目标
    """

    def parse_one(self, text: str) -> CotStep:
        """
        解析单个 CoT 四元组。
        """
        steps = self.parse_many(text)

        if not steps:
            raise ValueError("未能从 CoT 文本中解析出四元组。")

        return steps[0]

    def parse_many(self, text: str) -> List[CotStep]:
        """
        解析多个 CoT 步骤。
        """
        pattern = re.compile(
            r"(?:步骤\d+：\s*)?"
            r"\(\s*"
            r"数据库\s*:\s*(?P<database>.*?)\s*,\s*"
            r"处理对象\s*:\s*(?P<processing_objects>.*?)\s*,\s*"
            r"操作指令\s*:\s*(?P<operation_instruction>.*?)\s*,\s*"
            r"输出目标\s*:\s*(?P<output_target>.*?)\s*"
            r"\)",
            re.S,
        )

        steps: List[CotStep] = []

        for match in pattern.finditer(text):
            steps.append(
                CotStep(
                    database=self._clean(match.group("database")),
                    processing_objects=self._clean(match.group("processing_objects")),
                    operation_instruction=self._clean(match.group("operation_instruction")),
                    output_target=self._clean(match.group("output_target")),
                )
            )

        return steps

    def _clean(self, value: str) -> str:
        """清理字段内容。"""
        return value.strip().rstrip(",").strip()
