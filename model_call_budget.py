from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Iterator


class ModelCallLimitExceeded(RuntimeError):
    """Raised before an HTTP model request would exceed the active budget."""


@dataclass(frozen=True)
class ModelCallRecord:
    number: int
    role: str
    provider: str
    model: str


@dataclass
class ModelCallBudget:
    limit: int
    calls: int = 0
    records: list[ModelCallRecord] = field(default_factory=list)

    @property
    def remaining(self) -> int:
        return max(0, self.limit - self.calls)

    def consume(self, *, role: str, provider: str, model: str) -> int:
        if self.calls >= self.limit:
            raise ModelCallLimitExceeded(
                f"真实模型调用预算已用完：上限 {self.limit} 次；第 {self.limit + 1} 次请求未发送"
            )
        self.calls += 1
        self.records.append(
            ModelCallRecord(
                number=self.calls,
                role=role,
                provider=provider,
                model=model,
            )
        )
        return self.calls


_ACTIVE_BUDGET: ContextVar[ModelCallBudget | None] = ContextVar(
    "askdata_model_call_budget",
    default=None,
)


@contextmanager
def model_call_budget(limit: int) -> Iterator[ModelCallBudget]:
    """Apply a hard request budget to every model HTTP client in this context."""
    if limit < 0:
        raise ValueError("模型调用预算不能小于 0")
    budget = ModelCallBudget(limit=limit)
    token = _ACTIVE_BUDGET.set(budget)
    try:
        yield budget
    finally:
        _ACTIVE_BUDGET.reset(token)


def consume_model_call(*, role: str, provider: str, model: str) -> int | None:
    """Count one outbound call, or do nothing when no budget is active."""
    budget = _ACTIVE_BUDGET.get()
    if budget is None:
        return None
    return budget.consume(role=role, provider=provider, model=model)
