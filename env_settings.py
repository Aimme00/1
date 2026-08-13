from __future__ import annotations

import os
import hashlib
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
        except ValueError:
            # Hosting dashboards commonly create detected variables with an
            # empty or placeholder value.  A typo in an optional tuning value
            # must not make the complete serverless application unimportable.
            value = default
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


def llm_provider() -> str:
    """Resolve the provider without treating blank dashboard fields as values."""
    configured = env_text("ASKDATA_LLM_PROVIDER").lower()
    if configured in {"deepseek", "dashscope"}:
        return configured
    if env_text("DEEPSEEK_API_KEY"):
        return "deepseek"
    if env_text("DASHSCOPE_API_KEY"):
        return "dashscope"
    return "deepseek"


def session_secret() -> str:
    """Return a stable HMAC secret for the public demo.

    An explicit long secret is preferred.  For zero-config Vercel previews we
    derive one from deployment/database secrets so a missing optional field can
    never crash the whole serverless function.
    """
    explicit = env_text("ASKDATA_SESSION_SECRET", strip=False)
    if len(explicit) >= 32:
        return explicit
    seed = "|".join(
        value
        for value in (
            explicit,
            env_text("ASKDATA_QUOTA_SALT", strip=False),
            postgres_url(),
            env_text("DEEPSEEK_API_KEY", strip=False),
            env_text("DASHSCOPE_API_KEY", strip=False),
            env_text("VERCEL_PROJECT_ID"),
            env_text("VERCEL_PROJECT_PRODUCTION_URL"),
            env_text("VERCEL_URL"),
        )
        if value
    )
    if not seed:
        seed = "askdata-public-interview-demo"
    return hashlib.sha256(f"askdata-session-v1:{seed}".encode("utf-8")).hexdigest()


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


def vercel_environment_issues() -> list[str]:
    """Report deployment issues without preventing health/UI routes from loading."""
    if not is_vercel():
        return []

    missing: list[str] = []
    if not postgres_url():
        missing.append("ASKDATA_POSTGRES_URL（Neon pooled connection string）")
    email = env_text("ASKDATA_BOOTSTRAP_EMAIL")
    password = env_text("ASKDATA_BOOTSTRAP_PASSWORD", strip=False)
    if bool(email) != bool(password):
        missing.append("ASKDATA_BOOTSTRAP_EMAIL 与 ASKDATA_BOOTSTRAP_PASSWORD（可同时不填）")
    if password and len(password) < 8:
        missing.append("ASKDATA_BOOTSTRAP_PASSWORD（当前不足 8 位）")
    provider = llm_provider()
    if provider == "deepseek" and not env_text("DEEPSEEK_API_KEY"):
        missing.append("DEEPSEEK_API_KEY")
    elif provider == "dashscope" and not env_text("DASHSCOPE_API_KEY"):
        missing.append("DASHSCOPE_API_KEY")
    elif provider not in {"deepseek", "dashscope"}:
        missing.append("ASKDATA_LLM_PROVIDER（只能是 deepseek 或 dashscope）")

    database_type = env_text("ASKDATA_DATABASE_TYPE")
    if database_type and database_type.lower() != "postgres":
        missing.append("ASKDATA_DATABASE_TYPE（Vercel 必须为 postgres，或删除该变量）")

    return missing


def validate_vercel_environment(*, strict: bool = False) -> list[str]:
    """Compatibility wrapper; normal web startup is deliberately fail-soft."""
    issues = vercel_environment_issues()
    if strict and issues:
        detail = "；".join(issues)
        raise RuntimeError(f"Vercel 配置不完整，请一次性补齐：{detail}")
    return issues
