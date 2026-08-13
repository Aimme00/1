from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
import uuid
from dataclasses import dataclass
from typing import Dict, List, Optional

from .auth import (
    AuthUser,
    AuthenticationError,
    AuthenticationRateLimitError,
    InvalidBootstrapUserError,
)


@dataclass(frozen=True)
class SignedAuthConfig:
    email: str
    password: str
    display_name: str
    secret: str
    session_ttl_seconds: int = 86_400
    cookie_name: str = "askdata_session"
    cookie_secure: bool = True
    max_failed_attempts: int = 5
    failure_window_seconds: int = 600

    @classmethod
    def from_environment(cls) -> "SignedAuthConfig":
        email = os.getenv("ASKDATA_BOOTSTRAP_EMAIL", "").strip().lower()
        password = os.getenv("ASKDATA_BOOTSTRAP_PASSWORD", "")
        secret = os.getenv("ASKDATA_SESSION_SECRET", "").strip()
        if not email or not password:
            raise InvalidBootstrapUserError("Vercel 部署必须配置登录邮箱和密码")
        if len(password) < 8:
            raise InvalidBootstrapUserError("登录密码至少需要 8 个字符")
        if len(secret) < 32:
            raise InvalidBootstrapUserError("ASKDATA_SESSION_SECRET 至少需要 32 个字符")
        return cls(
            email=email,
            password=password,
            display_name=os.getenv("ASKDATA_BOOTSTRAP_DISPLAY_NAME", "Interview Demo").strip(),
            secret=secret,
            session_ttl_seconds=max(300, int(os.getenv("ASKDATA_SESSION_TTL_SECONDS", "86400"))),
        )


class SignedAuthService:
    """无服务器环境的 HMAC 签名会话；密码和 Secret 只来自环境变量。"""

    def __init__(self, config: SignedAuthConfig):
        self.config = config
        self._failed: Dict[str, List[float]] = {}

    def login(self, *, email: str, password: str, source: str = "unknown"):
        key = f"{source}:{email.strip().lower()}"
        self._check(key)
        email_ok = hmac.compare_digest(email.strip().lower(), self.config.email)
        password_ok = hmac.compare_digest(password, self.config.password)
        if not email_ok or not password_ok:
            self._failed.setdefault(key, []).append(time.monotonic())
            raise AuthenticationError("邮箱或密码错误")
        self._failed.pop(key, None)
        now = int(time.time())
        # 公开体验共用登录凭据，但每次登录生成独立用户空间，避免不同访客
        # 互相看到会话、保存分析和仪表盘。
        user = self._user(f"user_guest_{uuid.uuid4().hex}", now)
        payload = {"sub": user.id, "iat": now, "exp": now + self.config.session_ttl_seconds}
        encoded = self._encode(json.dumps(payload, separators=(",", ":")).encode())
        signature = self._sign(encoded)
        return user, f"{encoded}.{signature}"

    def get_user_for_token(self, token: str) -> Optional[AuthUser]:
        try:
            encoded, signature = token.split(".", 1)
            if not hmac.compare_digest(signature, self._sign(encoded)):
                return None
            payload = json.loads(self._decode(encoded))
            user_id = str(payload.get("sub") or "")
            issued_at = int(payload.get("iat", 0))
            if not user_id.startswith("user_guest_") or int(payload.get("exp", 0)) <= int(time.time()):
                return None
            return self._user(user_id, issued_at)
        except Exception:
            return None

    def logout(self, token: str) -> None:
        del token

    def _check(self, key: str) -> None:
        cutoff = time.monotonic() - self.config.failure_window_seconds
        attempts = [stamp for stamp in self._failed.get(key, []) if stamp > cutoff]
        self._failed[key] = attempts
        if len(attempts) >= self.config.max_failed_attempts:
            raise AuthenticationRateLimitError("登录失败次数过多，请稍后再试")

    def _sign(self, encoded: str) -> str:
        digest = hmac.new(self.config.secret.encode(), encoded.encode(), hashlib.sha256).digest()
        return self._encode(digest)

    def _user(self, user_id: str, created_at: int) -> AuthUser:
        return AuthUser(
            id=user_id,
            email=self.config.email,
            display_name=self.config.display_name or self.config.email.split("@", 1)[0],
            is_active=True,
            is_admin=True,
            created_at=created_at,
        )

    @staticmethod
    def _encode(value: bytes) -> str:
        return base64.urlsafe_b64encode(value).decode().rstrip("=")

    @staticmethod
    def _decode(value: str) -> str:
        padded = value + "=" * (-len(value) % 4)
        return base64.urlsafe_b64decode(padded).decode()
