from __future__ import annotations

import hashlib
import hmac
import os
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Dict

from env_settings import env_int, env_text, postgres_url

@dataclass(frozen=True)
class DemoQuotaConfig:
    db_path: Path
    query_limit: int = 2
    tester_token: str = ""
    fingerprint_salt: str = "askdata-local-quota"
    database_url: str = ""

    @classmethod
    def from_environment(cls, runtime_dir: str | Path) -> "DemoQuotaConfig":
        limit = env_int("ASKDATA_GUEST_QUERY_LIMIT", 2, minimum=1, maximum=100)
        return cls(
            db_path=Path(runtime_dir) / "demo_quota.db",
            query_limit=limit,
            tester_token=env_text("ASKDATA_TEST_TOKEN"),
            fingerprint_salt=env_text(
                "ASKDATA_QUOTA_SALT",
                env_text("ASKDATA_SESSION_SECRET", "askdata-local-quota"),
            ),
            database_url=postgres_url(),
        )


class DemoQuotaExceededError(RuntimeError):
    """公开体验额度已用完。"""


class DemoQuotaService:
    """以不可逆网络指纹记录公开体验次数，不保存原始 IP。"""

    def __init__(self, config: DemoQuotaConfig):
        self.config = config
        self.config.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        if self.config.database_url:
            return
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS demo_query_usage (
                    subject_hash TEXT PRIMARY KEY,
                    query_count INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )

    def status(self, *, subject: str, tester_token: str = "") -> Dict[str, object]:
        if self._is_tester(tester_token):
            return self._payload(used=0, unlimited=True)
        subject_hash = self._subject_hash(subject)
        if self.config.database_url:
            with self._pg_connect() as connection:
                row = connection.execute(
                    "SELECT query_count FROM _askdata_quota WHERE subject_hash = %s",
                    (subject_hash,),
                ).fetchone()
            return self._payload(used=int(row[0]) if row else 0)
        with self._connect() as connection:
            row = connection.execute(
                "SELECT query_count FROM demo_query_usage WHERE subject_hash = ?",
                (subject_hash,),
            ).fetchone()
        return self._payload(used=int(row[0]) if row else 0)

    def consume(self, *, subject: str, tester_token: str = "") -> Dict[str, object]:
        if self._is_tester(tester_token):
            return self._payload(used=0, unlimited=True)
        subject_hash = self._subject_hash(subject)
        if self.config.database_url:
            with self._pg_connect() as connection:
                row = connection.execute(
                    """INSERT INTO _askdata_quota(subject_hash,query_count,updated_at)
                       VALUES (%s,1,NOW()) ON CONFLICT(subject_hash) DO UPDATE SET
                         query_count=_askdata_quota.query_count+1,updated_at=NOW()
                       WHERE _askdata_quota.query_count < %s
                       RETURNING query_count""",
                    (subject_hash, self.config.query_limit),
                ).fetchone()
                if row is None:
                    raise DemoQuotaExceededError(
                        f"本次公开体验的 {self.config.query_limit} 次提问已使用完"
                    )
                used = int(row[0])
            return self._payload(used=used)
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT query_count FROM demo_query_usage WHERE subject_hash = ?",
                (subject_hash,),
            ).fetchone()
            used = int(row[0]) if row else 0
            if used >= self.config.query_limit:
                raise DemoQuotaExceededError(
                    f"本次公开体验的 {self.config.query_limit} 次提问已使用完"
                )
            used += 1
            connection.execute(
                """
                INSERT INTO demo_query_usage(subject_hash, query_count, updated_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(subject_hash) DO UPDATE SET
                    query_count = excluded.query_count,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (subject_hash, used),
            )
            connection.commit()
        return self._payload(used=used)

    def _is_tester(self, tester_token: str) -> bool:
        configured = self.config.tester_token
        provided = tester_token.strip()
        return bool(configured and provided and hmac.compare_digest(configured, provided))

    def _payload(self, *, used: int, unlimited: bool = False) -> Dict[str, object]:
        limit = self.config.query_limit
        return {
            "limit": limit,
            "used": used,
            "remaining": limit if unlimited else max(0, limit - used),
            "unlimited": unlimited,
        }

    def _subject_hash(self, subject: str) -> str:
        normalized = subject.strip() or "unknown"
        return hmac.new(
            self.config.fingerprint_salt.encode("utf-8"),
            normalized.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(str(self.config.db_path), timeout=30)

    def _pg_connect(self):
        import psycopg

        return psycopg.connect(self.config.database_url)
