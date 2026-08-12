from __future__ import annotations

import os
from dataclasses import dataclass


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
    selected = (provider or os.getenv("ASKDATA_LLM_PROVIDER", "")).strip().lower()
    if not selected:
        selected = "deepseek" if os.getenv("DEEPSEEK_API_KEY") else "dashscope"
    if selected not in {"deepseek", "dashscope"}:
        raise ValueError("ASKDATA_LLM_PROVIDER 仅支持 deepseek 或 dashscope。")

    role_name = "COT" if role.lower() == "cot" else "CODER"
    if selected == "deepseek":
        resolved_key = api_key or os.getenv("DEEPSEEK_API_KEY", "")
        resolved_url = base_url or os.getenv(
            "DEEPSEEK_BASE_URL", "https://api.deepseek.com/chat/completions"
        )
        resolved_model = model or os.getenv(
            f"DEEPSEEK_{role_name}_MODEL", "deepseek-v4-flash"
        )
    else:
        resolved_key = api_key or os.getenv("DASHSCOPE_API_KEY", "")
        resolved_url = base_url or os.getenv(
            "DASHSCOPE_BASE_URL",
            "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
        )
        resolved_model = model or os.getenv(
            f"DASHSCOPE_{role_name}_MODEL", "qwen-plus"
        )
    return ModelProviderSettings(
        provider=selected,
        api_key=resolved_key,
        base_url=resolved_url,
        model=resolved_model,
    )


def has_model_api_key() -> bool:
    selected = os.getenv("ASKDATA_LLM_PROVIDER", "").strip().lower()
    if selected == "deepseek":
        return bool(os.getenv("DEEPSEEK_API_KEY"))
    if selected == "dashscope":
        return bool(os.getenv("DASHSCOPE_API_KEY"))
    return bool(os.getenv("DEEPSEEK_API_KEY") or os.getenv("DASHSCOPE_API_KEY"))


def allow_mock_model() -> bool:
    """Local development may use deterministic mocks; production fails closed by default."""
    configured = os.getenv("ASKDATA_ALLOW_MOCK_MODEL", "").strip().lower()
    if configured:
        return configured in {"1", "true", "yes", "on"}
    return os.getenv("ASKDATA_ENV", "development").strip().lower() != "production"
