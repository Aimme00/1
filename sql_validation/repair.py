from __future__ import annotations

from typing import Callable, Dict, Iterable, Optional

from .objects import SQLAttempt, SQLRepairOutcome
from .validator import SQLValidator


RepairCallback = Callable[[str, str, int], str]


class SQLValidationRepairLoop:
    """执行 SQL 校验，并把结构化错误最多反馈给生成器指定次数。"""

    def __init__(self, validator: SQLValidator, max_repair_attempts: int = 2):
        if max_repair_attempts < 0:
            raise ValueError("max_repair_attempts 不能小于 0")
        self.validator = validator
        self.max_repair_attempts = max_repair_attempts

    def run(
        self,
        initial_sql: str,
        *,
        repair: RepairCallback,
        allowed_tables: Optional[Iterable[str]] = None,
        allowed_columns: Optional[Dict[str, Iterable[str]]] = None,
    ) -> SQLRepairOutcome:
        current_sql = initial_sql
        attempts: list[SQLAttempt] = []

        for number in range(1, self.max_repair_attempts + 2):
            validation = self.validator.validate(
                current_sql,
                allowed_tables=allowed_tables,
                allowed_columns=allowed_columns,
            )
            attempts.append(SQLAttempt(number=number, sql=current_sql, validation=validation))
            if validation.is_valid:
                return SQLRepairOutcome(
                    sql=validation.validated_sql,
                    validation=validation,
                    attempts=attempts,
                )
            if number > self.max_repair_attempts:
                break
            current_sql = repair(current_sql, validation.feedback_text(), number)

        return SQLRepairOutcome(
            sql=current_sql,
            validation=attempts[-1].validation,
            attempts=attempts,
        )
