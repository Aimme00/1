from __future__ import annotations

import os
from dataclasses import dataclass

from env_settings import env_text, llm_provider, production_environment

@dataclass(frozen=True)
class ModelProviderSettings:
    provider: str
    api_key: str
    base_url: str
    model: str


def resolve_model_settings(
    *,
    role: str,
    provider: str = "",
    api_key: str = "",
    base_url: str = "",
    model: str = "",
) -> ModelProviderSettings:
    """Resolve DeepSeek or DashScope settings without ever logging the secret."""
    selected = (provider or llm_provider()).strip().lower()
    if selected not in {"deepseek", "dashscope"}:
        selected = llm_provider()

    role_name = "COT" if role.lower() == "cot" else "CODER"
    if selected == "deepseek":
        resolved_key = api_key or env_text("DEEPSEEK_API_KEY")
        resolved_url = base_url or env_text(
            "DEEPSEEK_BASE_URL", "https://api.deepseek.com/chat/completions"
        )
        resolved_model = model or env_text(
            f"DEEPSEEK_{role_name}_MODEL", "deepseek-v4-flash"
        )
    else:
        resolved_key = api_key or env_text("DASHSCOPE_API_KEY")
        resolved_url = base_url or env_text(
            "DASHSCOPE_BASE_URL",
            "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
        )
        resolved_model = model or env_text(
            f"DASHSCOPE_{role_name}_MODEL", "qwen-plus"
        )
    return ModelProviderSettings(
        provider=selected,
        api_key=resolved_key,
        base_url=resolved_url,
        model=resolved_model,
    )


def has_model_api_key() -> bool:
    selected = llm_provider()
    if selected == "deepseek":
        return bool(env_text("DEEPSEEK_API_KEY"))
    if selected == "dashscope":
        return bool(env_text("DASHSCOPE_API_KEY"))
    return bool(env_text("DEEPSEEK_API_KEY") or env_text("DASHSCOPE_API_KEY"))


def allow_mock_model() -> bool:
    """Use deterministic SQL planning when no billable model key is configured."""
    configured = env_text("ASKDATA_ALLOW_MOCK_MODEL").lower()
    if configured:
        return configured in {"1", "true", "yes", "on"}
    return not production_environment() or not has_model_api_key()
