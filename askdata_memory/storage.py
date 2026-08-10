from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Sequence

import numpy as np

from .objects import ConversationSummary, LongTermMemory, MemoryKind, MemoryMessage, MessageRole


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class SQLiteMemoryStore:
    """SQLite 持久化实现；连接按操作创建，可安全用于摘要后台线程。"""

    def __init__(self, db_path: str | Path = "runtime_data/memory.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._schema_lock = threading.Lock()
        self._initialize()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(str(self.db_path), timeout=30)
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
                CREATE TABLE IF NOT EXISTS memory_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    message_type TEXT NOT NULL DEFAULT 'text',
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    token_count INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_memory_messages_session
                    ON memory_messages(user_id, session_id, id);

                CREATE TABLE IF NOT EXISTS conversation_summaries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    content TEXT NOT NULL,
                    through_message_id INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(user_id, session_id, version)
                );
                CREATE INDEX IF NOT EXISTS idx_conversation_summary_latest
                    ON conversation_summaries(user_id, session_id, version DESC);

                CREATE TABLE IF NOT EXISTS long_term_memories (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    content_json TEXT NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    embedding BLOB NOT NULL,
                    embedding_dimensions INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_long_term_memory_user
                    ON long_term_memories(user_id, kind, created_at DESC);

                CREATE TABLE IF NOT EXISTS saved_analyses (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    query TEXT NOT NULL,
                    result_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(user_id, run_id)
                );
                CREATE INDEX IF NOT EXISTS idx_saved_analyses_user
                    ON saved_analyses(user_id, updated_at DESC);

                CREATE TABLE IF NOT EXISTS dashboards (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_dashboards_user
                    ON dashboards(user_id, updated_at DESC);

                CREATE TABLE IF NOT EXISTS dashboard_cards (
                    id TEXT PRIMARY KEY,
                    dashboard_id TEXT NOT NULL REFERENCES dashboards(id) ON DELETE CASCADE,
                    analysis_id TEXT NOT NULL REFERENCES saved_analyses(id) ON DELETE CASCADE,
                    title TEXT NOT NULL,
                    position INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    UNIQUE(dashboard_id, analysis_id)
                );
                CREATE INDEX IF NOT EXISTS idx_dashboard_cards_order
                    ON dashboard_cards(dashboard_id, position, created_at);

                """
            )

    def add_message(
        self,
        *,
        session_id: str,
        user_id: str,
        role: MessageRole,
        content: str,
        message_type: str = "text",
        payload: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        token_count: int = 0,
    ) -> MemoryMessage:
        created_at = _utc_now()
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO memory_messages(
                    session_id, user_id, role, content, message_type,
                    payload_json, metadata_json, token_count, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    user_id,
                    role.value,
                    content,
                    message_type,
                    json.dumps(payload or {}, ensure_ascii=False, default=str),
                    json.dumps(metadata or {}, ensure_ascii=False, default=str),
                    token_count,
                    created_at,
                ),
            )
            message_id = int(cursor.lastrowid)
        return MemoryMessage(
            id=message_id,
            session_id=session_id,
            user_id=user_id,
            role=role,
            content=content,
            message_type=message_type,
            payload=payload or {},
            metadata=metadata or {},
            token_count=token_count,
            created_at=created_at,
        )

    def list_messages(self, *, user_id: str, session_id: str) -> List[MemoryMessage]:
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT * FROM memory_messages
                   WHERE user_id = ? AND session_id = ? ORDER BY id ASC""",
                (user_id, session_id),
            ).fetchall()
        return [self._row_to_message(row) for row in rows]

    def list_sessions(self, *, user_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        """返回用户会话摘要，供网页侧边栏使用。"""
        safe_limit = max(1, min(int(limit), 200))
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT
                    messages.session_id,
                    messages.created_at,
                    messages.updated_at,
                    messages.message_count,
                    COALESCE(
                        (SELECT content FROM memory_messages first_user
                         WHERE first_user.user_id = ?
                           AND first_user.session_id = messages.session_id
                           AND first_user.role = 'user'
                         ORDER BY first_user.id ASC LIMIT 1),
                        '新会话'
                    ) AS title
                FROM (
                    SELECT session_id,
                           MIN(created_at) AS created_at,
                           MAX(created_at) AS updated_at,
                           COUNT(*) AS message_count,
                           MAX(id) AS latest_message_id
                    FROM memory_messages
                    WHERE user_id = ?
                    GROUP BY session_id
                ) messages
                ORDER BY messages.latest_message_id DESC
                LIMIT ?
                """,
                (user_id, user_id, safe_limit),
            ).fetchall()
        return [
            {
                "session_id": row["session_id"],
                "title": row["title"][:60],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
                "message_count": int(row["message_count"]),
            }
            for row in rows
        ]

    def save_analysis(
        self,
        *,
        user_id: str,
        session_id: str,
        run_id: str,
        title: str,
        query: str,
        result: Dict[str, Any],
    ) -> Dict[str, Any]:
        analysis_id = f"analysis_{uuid.uuid4().hex}"
        now = _utc_now()
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT id, created_at FROM saved_analyses WHERE user_id = ? AND run_id = ?",
                (user_id, run_id),
            ).fetchone()
            if existing:
                analysis_id = existing["id"]
                created_at = existing["created_at"]
                conn.execute(
                    """
                    UPDATE saved_analyses
                    SET session_id = ?, title = ?, query = ?, result_json = ?, updated_at = ?
                    WHERE id = ? AND user_id = ?
                    """,
                    (
                        session_id,
                        title,
                        query,
                        json.dumps(result, ensure_ascii=False, default=str),
                        now,
                        analysis_id,
                        user_id,
                    ),
                )
            else:
                created_at = now
                conn.execute(
                    """
                    INSERT INTO saved_analyses(
                        id, user_id, session_id, run_id, title, query,
                        result_json, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        analysis_id,
                        user_id,
                        session_id,
                        run_id,
                        title,
                        query,
                        json.dumps(result, ensure_ascii=False, default=str),
                        created_at,
                        now,
                    ),
                )
        return {
            "id": analysis_id,
            "user_id": user_id,
            "session_id": session_id,
            "run_id": run_id,
            "title": title,
            "query": query,
            "result": result,
            "created_at": created_at,
            "updated_at": now,
        }

    def list_saved_analyses(
        self,
        *,
        user_id: str,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        safe_limit = max(1, min(int(limit), 200))
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, user_id, session_id, run_id, title, query,
                       created_at, updated_at
                FROM saved_analyses
                WHERE user_id = ?
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (user_id, safe_limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_saved_analysis(
        self,
        *,
        user_id: str,
        analysis_id: str,
    ) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM saved_analyses WHERE user_id = ? AND id = ?",
                (user_id, analysis_id),
            ).fetchone()
        if row is None:
            return None
        payload = dict(row)
        payload["result"] = json.loads(payload.pop("result_json"))
        return payload

    def delete_saved_analysis(self, *, user_id: str, analysis_id: str) -> bool:
        with self._connect() as conn:
            cursor = conn.execute(
                "DELETE FROM saved_analyses WHERE user_id = ? AND id = ?",
                (user_id, analysis_id),
            )
        return cursor.rowcount > 0

    def create_dashboard(
        self, *, user_id: str, name: str, description: str = ""
    ) -> Dict[str, Any]:
        dashboard_id = f"dashboard_{uuid.uuid4().hex}"
        now = _utc_now()
        safe_name = name.strip()[:80] or "我的仪表盘"
        safe_description = description.strip()[:300]
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO dashboards(
                       id, user_id, name, description, created_at, updated_at
                   ) VALUES (?, ?, ?, ?, ?, ?)""",
                (dashboard_id, user_id, safe_name, safe_description, now, now),
            )
        return {
            "id": dashboard_id,
            "user_id": user_id,
            "name": safe_name,
            "description": safe_description,
            "card_count": 0,
            "created_at": now,
            "updated_at": now,
        }

    def list_dashboards(
        self, *, user_id: str, limit: int = 50
    ) -> List[Dict[str, Any]]:
        safe_limit = max(1, min(int(limit), 100))
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT dashboards.id, dashboards.user_id, dashboards.name,
                       dashboards.description, dashboards.created_at,
                       dashboards.updated_at, COUNT(dashboard_cards.id) AS card_count
                FROM dashboards
                LEFT JOIN dashboard_cards
                  ON dashboard_cards.dashboard_id = dashboards.id
                WHERE dashboards.user_id = ?
                GROUP BY dashboards.id
                ORDER BY dashboards.updated_at DESC
                LIMIT ?
                """,
                (user_id, safe_limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_dashboard(
        self, *, user_id: str, dashboard_id: str
    ) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            dashboard = conn.execute(
                "SELECT * FROM dashboards WHERE user_id = ? AND id = ?",
                (user_id, dashboard_id),
            ).fetchone()
            if dashboard is None:
                return None
            rows = conn.execute(
                """
                SELECT dashboard_cards.id, dashboard_cards.analysis_id,
                       dashboard_cards.title, dashboard_cards.position,
                       dashboard_cards.created_at, saved_analyses.query,
                       saved_analyses.result_json,
                       saved_analyses.updated_at AS analysis_updated_at
                FROM dashboard_cards
                JOIN saved_analyses ON saved_analyses.id = dashboard_cards.analysis_id
                WHERE dashboard_cards.dashboard_id = ?
                  AND saved_analyses.user_id = ?
                ORDER BY dashboard_cards.position, dashboard_cards.created_at
                """,
                (dashboard_id, user_id),
            ).fetchall()
        payload = dict(dashboard)
        payload["cards"] = []
        for row in rows:
            card = dict(row)
            card["result"] = json.loads(card.pop("result_json"))
            payload["cards"].append(card)
        payload["card_count"] = len(payload["cards"])
        return payload

    def add_dashboard_card(
        self,
        *,
        user_id: str,
        dashboard_id: str,
        analysis_id: str,
        title: str = "",
    ) -> Optional[Dict[str, Any]]:
        now = _utc_now()
        with self._connect() as conn:
            dashboard = conn.execute(
                "SELECT id FROM dashboards WHERE user_id = ? AND id = ?",
                (user_id, dashboard_id),
            ).fetchone()
            analysis = conn.execute(
                "SELECT id, title FROM saved_analyses WHERE user_id = ? AND id = ?",
                (user_id, analysis_id),
            ).fetchone()
            if dashboard is None or analysis is None:
                return None
            existing = conn.execute(
                """SELECT id, title, position, created_at FROM dashboard_cards
                   WHERE dashboard_id = ? AND analysis_id = ?""",
                (dashboard_id, analysis_id),
            ).fetchone()
            if existing:
                return {
                    **dict(existing),
                    "dashboard_id": dashboard_id,
                    "analysis_id": analysis_id,
                }
            position = int(
                conn.execute(
                    "SELECT COALESCE(MAX(position), -1) + 1 FROM dashboard_cards WHERE dashboard_id = ?",
                    (dashboard_id,),
                ).fetchone()[0]
            )
            card_id = f"card_{uuid.uuid4().hex}"
            safe_title = title.strip()[:120] or analysis["title"]
            conn.execute(
                """INSERT INTO dashboard_cards(
                       id, dashboard_id, analysis_id, title, position, created_at
                   ) VALUES (?, ?, ?, ?, ?, ?)""",
                (card_id, dashboard_id, analysis_id, safe_title, position, now),
            )
            conn.execute(
                "UPDATE dashboards SET updated_at = ? WHERE id = ?",
                (now, dashboard_id),
            )
        return {
            "id": card_id,
            "dashboard_id": dashboard_id,
            "analysis_id": analysis_id,
            "title": safe_title,
            "position": position,
            "created_at": now,
        }

    def remove_dashboard_card(
        self, *, user_id: str, dashboard_id: str, card_id: str
    ) -> bool:
        now = _utc_now()
        with self._connect() as conn:
            cursor = conn.execute(
                """
                DELETE FROM dashboard_cards
                WHERE id = ? AND dashboard_id = ?
                  AND EXISTS (
                    SELECT 1 FROM dashboards
                    WHERE dashboards.id = dashboard_cards.dashboard_id
                      AND dashboards.user_id = ?
                  )
                """,
                (card_id, dashboard_id, user_id),
            )
            if cursor.rowcount:
                conn.execute(
                    "UPDATE dashboards SET updated_at = ? WHERE id = ? AND user_id = ?",
                    (now, dashboard_id, user_id),
                )
        return cursor.rowcount > 0

    def delete_dashboard(self, *, user_id: str, dashboard_id: str) -> bool:
        with self._connect() as conn:
            cursor = conn.execute(
                "DELETE FROM dashboards WHERE user_id = ? AND id = ?",
                (user_id, dashboard_id),
            )
        return cursor.rowcount > 0

    def get_latest_summary(
        self, *, user_id: str, session_id: str
    ) -> Optional[ConversationSummary]:
        with self._connect() as conn:
            row = conn.execute(
                """SELECT * FROM conversation_summaries
                   WHERE user_id = ? AND session_id = ?
                   ORDER BY version DESC LIMIT 1""",
                (user_id, session_id),
            ).fetchone()
        if row is None:
            return None
        return ConversationSummary(
            id=int(row["id"]),
            session_id=row["session_id"],
            user_id=row["user_id"],
            version=int(row["version"]),
            content=row["content"],
            through_message_id=int(row["through_message_id"]),
            created_at=row["created_at"],
        )

    def save_summary(
        self,
        *,
        user_id: str,
        session_id: str,
        content: str,
        through_message_id: int,
    ) -> ConversationSummary:
        created_at = _utc_now()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """SELECT COALESCE(MAX(version), 0) AS version
                   FROM conversation_summaries
                   WHERE user_id = ? AND session_id = ?""",
                (user_id, session_id),
            ).fetchone()
            version = int(row["version"]) + 1
            cursor = conn.execute(
                """INSERT INTO conversation_summaries(
                       session_id, user_id, version, content,
                       through_message_id, created_at
                   ) VALUES (?, ?, ?, ?, ?, ?)""",
                (session_id, user_id, version, content, through_message_id, created_at),
            )
            summary_id = int(cursor.lastrowid)
        return ConversationSummary(
            id=summary_id,
            session_id=session_id,
            user_id=user_id,
            version=version,
            content=content,
            through_message_id=through_message_id,
            created_at=created_at,
        )

    def save_long_term_memory(
        self,
        *,
        user_id: str,
        kind: MemoryKind,
        summary: str,
        content: Any,
        metadata: Dict[str, Any],
        embedding: np.ndarray,
        memory_id: Optional[str] = None,
    ) -> LongTermMemory:
        memory_id = memory_id or str(uuid.uuid4())
        created_at = _utc_now()
        vector = np.asarray(embedding, dtype=np.float32).reshape(-1)
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO long_term_memories(
                       id, user_id, kind, summary, content_json, metadata_json,
                       embedding, embedding_dimensions, created_at, updated_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    memory_id,
                    user_id,
                    kind.value,
                    summary,
                    json.dumps(content, ensure_ascii=False, default=str),
                    json.dumps(metadata, ensure_ascii=False, default=str),
                    vector.tobytes(),
                    int(vector.size),
                    created_at,
                    created_at,
                ),
            )
        return LongTermMemory(
            id=memory_id,
            user_id=user_id,
            kind=kind,
            summary=summary,
            content=content,
            metadata=metadata,
            embedding=vector,
            created_at=created_at,
            updated_at=created_at,
        )

    def list_long_term_memories(
        self, *, user_id: str, kinds: Optional[Sequence[MemoryKind]] = None
    ) -> List[LongTermMemory]:
        params: List[Any] = [user_id]
        sql = "SELECT * FROM long_term_memories WHERE user_id = ?"
        if kinds:
            placeholders = ",".join("?" for _ in kinds)
            sql += f" AND kind IN ({placeholders})"
            params.extend(kind.value for kind in kinds)
        sql += " ORDER BY created_at DESC"
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [self._row_to_long_term(row) for row in rows]

    def get_long_term_memory(self, *, user_id: str, memory_id: str) -> Optional[LongTermMemory]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM long_term_memories WHERE user_id = ? AND id = ?",
                (user_id, memory_id),
            ).fetchone()
        return self._row_to_long_term(row) if row else None

    def delete_long_term_memory(self, *, user_id: str, memory_id: str) -> bool:
        with self._connect() as conn:
            cursor = conn.execute(
                "DELETE FROM long_term_memories WHERE user_id = ? AND id = ?",
                (user_id, memory_id),
            )
        return cursor.rowcount > 0

    @staticmethod
    def _row_to_message(row: sqlite3.Row) -> MemoryMessage:
        return MemoryMessage(
            id=int(row["id"]),
            session_id=row["session_id"],
            user_id=row["user_id"],
            role=MessageRole(row["role"]),
            content=row["content"],
            message_type=row["message_type"],
            payload=json.loads(row["payload_json"]),
            metadata=json.loads(row["metadata_json"]),
            token_count=int(row["token_count"]),
            created_at=row["created_at"],
        )

    @staticmethod
    def _row_to_long_term(row: sqlite3.Row) -> LongTermMemory:
        dimensions = int(row["embedding_dimensions"])
        embedding = np.frombuffer(row["embedding"], dtype=np.float32, count=dimensions).copy()
        return LongTermMemory(
            id=row["id"],
            user_id=row["user_id"],
            kind=MemoryKind(row["kind"]),
            summary=row["summary"],
            content=json.loads(row["content_json"]),
            metadata=json.loads(row["metadata_json"]),
            embedding=embedding,
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
