from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable


TRUE_VALUES = {"1", "true", "yes", "on"}


def env_text(name: str, default: str = "", *, strip: bool = True) -> str:
    """Return a useful environment value; blank values behave as unset values."""
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    return value.strip() if strip else value


def first_env(names: Iterable[str], default: str = "") -> str:
    for name in names:
        value = env_text(name)
        if value:
            return value
    return default


def env_int(
    name: str,
    default: int,
    *,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    raw = env_text(name)
    if not raw:
        value = default
    else:
        try:
            value = int(raw)
        except ValueError as exc:
            raise ValueError(f"{name} 必须是整数，当前值无法解析") from exc
    if minimum is not None:
        value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value


def env_bool(name: str, default: bool) -> bool:
    raw = env_text(name).lower()
    return default if not raw else raw in TRUE_VALUES


def is_vercel() -> bool:
    return bool(env_text("VERCEL"))


def postgres_url() -> str:
    return first_env(("ASKDATA_POSTGRES_URL", "POSTGRES_URL", "DATABASE_URL"))


def runtime_dir(project_root: str | Path) -> Path:
    configured = env_text("ASKDATA_RUNTIME_DIR")
    if configured:
        return Path(configured)
    # Vercel 函数的项目目录只读，只有 /tmp 可写。
    if is_vercel():
        return Path("/tmp/askdata_runtime")
    return Path(project_root) / "runtime_data"


def production_environment() -> bool:
    return env_text("ASKDATA_ENV", "production" if is_vercel() else "development").lower() == "production"


def validate_vercel_environment() -> None:
    """Fail once with every missing Vercel secret instead of one error per deploy."""
    if not is_vercel():
        return

    missing: list[str] = []
    if not postgres_url():
        missing.append("ASKDATA_POSTGRES_URL（Neon pooled connection string）")
    email = env_text("ASKDATA_BOOTSTRAP_EMAIL")
    password = env_text("ASKDATA_BOOTSTRAP_PASSWORD", strip=False)
    if bool(email) != bool(password):
        missing.append("ASKDATA_BOOTSTRAP_EMAIL 与 ASKDATA_BOOTSTRAP_PASSWORD（可同时不填）")
    if password and len(password) < 8:
        missing.append("ASKDATA_BOOTSTRAP_PASSWORD（当前不足 8 位）")
    if len(env_text("ASKDATA_SESSION_SECRET")) < 32:
        missing.append("ASKDATA_SESSION_SECRET（至少 32 位随机字符串）")

    provider = env_text("ASKDATA_LLM_PROVIDER")
    if not provider:
        provider = "deepseek" if env_text("DEEPSEEK_API_KEY") else "dashscope"
    if provider == "deepseek" and not env_text("DEEPSEEK_API_KEY"):
        missing.append("DEEPSEEK_API_KEY")
    elif provider == "dashscope" and not env_text("DASHSCOPE_API_KEY"):
        missing.append("DASHSCOPE_API_KEY")
    elif provider not in {"deepseek", "dashscope"}:
        missing.append("ASKDATA_LLM_PROVIDER（只能是 deepseek 或 dashscope）")

    database_type = env_text("ASKDATA_DATABASE_TYPE")
    if database_type and database_type.lower() != "postgres":
        missing.append("ASKDATA_DATABASE_TYPE（Vercel 必须为 postgres，或删除该变量）")

    if missing:
        detail = "；".join(missing)
        raise RuntimeError(f"Vercel 配置不完整，请一次性补齐：{detail}")
