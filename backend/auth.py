from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterator, List, Optional


class AuthenticationError(ValueError):
    """邮箱或密码不正确。"""


class AuthenticationRateLimitError(RuntimeError):
    """短时间内登录失败次数过多。"""


class InvalidBootstrapUserError(ValueError):
    """初始化管理员配置不符合最低安全要求。"""


@dataclass(frozen=True)
class AuthUser:
    id: str
    email: str
    display_name: str
    is_active: bool
    is_admin: bool
    created_at: int

    def to_dict(self) -> Dict[str, object]:
        return {
            "id": self.id,
            "email": self.email,
            "display_name": self.display_name,
            "is_active": self.is_active,
            "is_admin": self.is_admin,
            "created_at": self.created_at,
        }


@dataclass(frozen=True)
class AuthConfig:
    db_path: Path
    environment: str = "development"
    session_ttl_seconds: int = 24 * 60 * 60
    pbkdf2_iterations: int = 600_000
    max_failed_attempts: int = 5
    failure_window_seconds: int = 10 * 60
    cookie_name: str = "askdata_session"
    cookie_secure: bool = False
    bootstrap_email: str = ""
    bootstrap_password: str = ""
    bootstrap_display_name: str = ""

    @classmethod
    def from_environment(cls, runtime_dir: str | Path) -> "AuthConfig":
        environment = os.getenv("ASKDATA_ENV", "development").strip().lower()
        is_production = environment == "production"
        bootstrap_email = os.getenv("ASKDATA_BOOTSTRAP_EMAIL", "").strip()
        bootstrap_password = os.getenv("ASKDATA_BOOTSTRAP_PASSWORD", "")
        bootstrap_display_name = os.getenv("ASKDATA_BOOTSTRAP_DISPLAY_NAME", "").strip()
        cookie_setting = os.getenv("ASKDATA_COOKIE_SECURE", "").strip().lower()
        if not is_production:
            bootstrap_email = bootstrap_email or "demo@askdata.local"
            bootstrap_password = bootstrap_password or "askdata-demo"
            bootstrap_display_name = bootstrap_display_name or "Demo Analyst"
        return cls(
            db_path=Path(runtime_dir) / "auth.db",
            environment=environment,
            session_ttl_seconds=max(
                300, int(os.getenv("ASKDATA_SESSION_TTL_SECONDS", str(24 * 60 * 60)))
            ),
            cookie_secure=(
                is_production
                if not cookie_setting
                else cookie_setting in {"1", "true", "yes", "on"}
            ),
            bootstrap_email=bootstrap_email,
            bootstrap_password=bootstrap_password,
            bootstrap_display_name=bootstrap_display_name,
        )


class AuthService:
    """SQLite 用户与不透明会话令牌服务。浏览器只保存 HttpOnly Cookie。"""

    def __init__(self, config: AuthConfig):
        self.config = config
        self.config.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._schema_lock = threading.Lock()
        self._attempt_lock = threading.Lock()
        self._failed_attempts: Dict[str, List[float]] = {}
        self._initialize()
        if config.environment == "production" and not config.cookie_secure:
            raise InvalidBootstrapUserError("生产环境必须启用 Secure Cookie")
        if config.environment == "production" and (
            config.bootstrap_email == "demo@askdata.local"
            or config.bootstrap_password == "askdata-demo"
        ):
            raise InvalidBootstrapUserError("生产环境禁止使用演示账号或演示密码")
        if config.bootstrap_email or config.bootstrap_password:
            if not config.bootstrap_email or not config.bootstrap_password:
                raise InvalidBootstrapUserError("初始化邮箱和密码必须同时设置")
            self.ensure_bootstrap_user(
                email=config.bootstrap_email,
                password=config.bootstrap_password,
                display_name=config.bootstrap_display_name,
            )
        elif config.environment == "production" and not self._has_users():
            raise InvalidBootstrapUserError("生产环境首次启动必须配置初始化管理员")

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(str(self.config.db_path), timeout=30)
        try:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("PRAGMA journal_mode = WAL")
            with conn:
                yield conn
        finally:
            conn.close()

    def _initialize(self) -> None:
        with self._schema_lock, self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS auth_users (
                    id TEXT PRIMARY KEY,
                    email TEXT NOT NULL UNIQUE COLLATE NOCASE,
                    display_name TEXT NOT NULL,
                    password_hash BLOB NOT NULL,
                    password_salt BLOB NOT NULL,
                    password_iterations INTEGER NOT NULL,
                    is_active INTEGER NOT NULL DEFAULT 1,
                    is_admin INTEGER NOT NULL DEFAULT 0,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS auth_sessions (
                    token_hash TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL REFERENCES auth_users(id) ON DELETE CASCADE,
                    created_at INTEGER NOT NULL,
                    expires_at INTEGER NOT NULL,
                    revoked_at INTEGER
                );
                CREATE INDEX IF NOT EXISTS idx_auth_sessions_user
                    ON auth_sessions(user_id, expires_at DESC);
                """
            )
            columns = {
                row["name"]
                for row in conn.execute("PRAGMA table_info(auth_users)").fetchall()
            }
            if "is_admin" not in columns:
                conn.execute(
                    "ALTER TABLE auth_users ADD COLUMN is_admin INTEGER NOT NULL DEFAULT 0"
                )

    def _has_users(self) -> bool:
        with self._connect() as conn:
            row = conn.execute("SELECT 1 FROM auth_users LIMIT 1").fetchone()
        return row is not None

    def ensure_bootstrap_user(
        self,
        *,
        email: str,
        password: str,
        display_name: str = "",
    ) -> AuthUser:
        normalized_email = self._normalize_email(email)
        if len(password) < 8:
            raise InvalidBootstrapUserError("初始化密码至少需要 8 个字符")
        now = int(time.time())
        salt = secrets.token_bytes(16)
        password_hash = self._hash_password(password, salt, self.config.pbkdf2_iterations)
        safe_name = display_name.strip()[:80] or normalized_email.split("@", 1)[0]
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id, created_at FROM auth_users WHERE email = ?",
                (normalized_email,),
            ).fetchone()
            if row:
                user_id = row["id"]
                created_at = int(row["created_at"])
                conn.execute(
                    """
                    UPDATE auth_users
                    SET display_name = ?, password_hash = ?, password_salt = ?,
                        password_iterations = ?, is_active = 1, is_admin = 1, updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        safe_name,
                        password_hash,
                        salt,
                        self.config.pbkdf2_iterations,
                        now,
                        user_id,
                    ),
                )
            else:
                user_id = f"user_{uuid.uuid4().hex}"
                created_at = now
                conn.execute(
                    """
                    INSERT INTO auth_users(
                        id, email, display_name, password_hash, password_salt,
                        password_iterations, is_active, is_admin, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, 1, 1, ?, ?)
                    """,
                    (
                        user_id,
                        normalized_email,
                        safe_name,
                        password_hash,
                        salt,
                        self.config.pbkdf2_iterations,
                        created_at,
                        now,
                    ),
                )
        return AuthUser(
            id=user_id,
            email=normalized_email,
            display_name=safe_name,
            is_active=True,
            is_admin=True,
            created_at=created_at,
        )

    def login(self, *, email: str, password: str, source: str = "unknown") -> tuple[AuthUser, str]:
        normalized_email = self._normalize_email(email)
        attempt_key = f"{source}:{normalized_email}"
        self._check_rate_limit(attempt_key)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM auth_users WHERE email = ?",
                (normalized_email,),
            ).fetchone()
        valid = False
        if row is not None and bool(row["is_active"]):
            candidate = self._hash_password(
                password,
                bytes(row["password_salt"]),
                int(row["password_iterations"]),
            )
            valid = hmac.compare_digest(candidate, bytes(row["password_hash"]))
        else:
            self._hash_password(password, b"askdata-no-user", self.config.pbkdf2_iterations)
        if not valid or row is None:
            self._record_failure(attempt_key)
            raise AuthenticationError("邮箱或密码错误")

        self._clear_failures(attempt_key)
        user = self._row_to_user(row)
        token = secrets.token_urlsafe(32)
        now = int(time.time())
        expires_at = now + self.config.session_ttl_seconds
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM auth_sessions WHERE expires_at <= ? OR revoked_at IS NOT NULL",
                (now,),
            )
            conn.execute(
                """
                INSERT INTO auth_sessions(token_hash, user_id, created_at, expires_at)
                VALUES (?, ?, ?, ?)
                """,
                (self._token_hash(token), user.id, now, expires_at),
            )
        return user, token

    def get_user_for_token(self, token: str) -> Optional[AuthUser]:
        if not token:
            return None
        now = int(time.time())
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT users.*
                FROM auth_sessions sessions
                JOIN auth_users users ON users.id = sessions.user_id
                WHERE sessions.token_hash = ?
                  AND sessions.revoked_at IS NULL
                  AND sessions.expires_at > ?
                  AND users.is_active = 1
                """,
                (self._token_hash(token), now),
            ).fetchone()
        return self._row_to_user(row) if row is not None else None

    def logout(self, token: str) -> None:
        if not token:
            return
        with self._connect() as conn:
            conn.execute(
                "UPDATE auth_sessions SET revoked_at = ? WHERE token_hash = ?",
                (int(time.time()), self._token_hash(token)),
            )

    def _check_rate_limit(self, key: str) -> None:
        cutoff = time.monotonic() - self.config.failure_window_seconds
        with self._attempt_lock:
            attempts = [stamp for stamp in self._failed_attempts.get(key, []) if stamp > cutoff]
            self._failed_attempts[key] = attempts
            if len(attempts) >= self.config.max_failed_attempts:
                raise AuthenticationRateLimitError("登录失败次数过多，请稍后再试")

    def _record_failure(self, key: str) -> None:
        with self._attempt_lock:
            self._failed_attempts.setdefault(key, []).append(time.monotonic())

    def _clear_failures(self, key: str) -> None:
        with self._attempt_lock:
            self._failed_attempts.pop(key, None)

    @staticmethod
    def _normalize_email(email: str) -> str:
        normalized = email.strip().lower()
        if not normalized or "@" not in normalized or len(normalized) > 254:
            raise AuthenticationError("请输入有效邮箱")
        return normalized

    @staticmethod
    def _hash_password(password: str, salt: bytes, iterations: int) -> bytes:
        return hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            salt,
            iterations,
        )

    @staticmethod
    def _token_hash(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    @staticmethod
    def _row_to_user(row: sqlite3.Row) -> AuthUser:
        return AuthUser(
            id=row["id"],
            email=row["email"],
            display_name=row["display_name"],
            is_active=bool(row["is_active"]),
            is_admin=bool(row["is_admin"]),
            created_at=int(row["created_at"]),
        )
