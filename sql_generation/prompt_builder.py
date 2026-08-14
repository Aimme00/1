from __future__ import annotations

from .objects import SqlGenerationRequest


class SqlPromptBuilder:
    """
    SQL 生成 Prompt 构建器。

    注意：
    - Prompt 中不放数据库名称，数据库名称只用于后续执行路由。
    """

    def build(self, request: SqlGenerationRequest) -> str:
        """
        构建 Coder 模型 Prompt。
        """
        cot_step = request.cot_step
        schema_context = request.local_schema.to_prompt_context()

        return f"""你是一个SQL生成助手。

请根据当前步骤的CoT规划和相关Schema信息生成SQL语句。

要求：
1. 只能使用Schema中提供的表、字段和表关联关系。
2. 严格按照CoT中的操作指令生成SQL。
3. 不得编造不存在的表、字段或关联关系。
4. 只输出SQL语句，不输出解释性内容。
5. SQL需要符合{request.sql_dialect}语法。
6. PostgreSQL 中使用 ROUND(expr, 位数) 时，expr 必须显式转换为 NUMERIC，
   例如 ROUND(CAST(SUM(sales_amount) AS NUMERIC), 2)。
7. PostgreSQL 日期计算使用 DATE_TRUNC、TO_CHAR 和 INTERVAL；禁止使用
   DATE_FORMAT、STRFTIME、JULIANDAY 等 MySQL/SQLite 函数。
8. 除法分母使用 NULLIF(分母, 0) 防止除零。
9. 不要输出Markdown代码块，不要输出```sql。

# 当前步骤CoT
处理对象：{cot_step.processing_objects}
操作指令：{cot_step.operation_instruction}
输出目标：{cot_step.output_target}

# Schema信息
{schema_context}
"""
