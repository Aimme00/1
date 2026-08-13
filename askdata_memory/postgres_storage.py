from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

import numpy as np

from .objects import ConversationSummary, LongTermMemory, MemoryKind, MemoryMessage, MessageRole


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class PostgresMemoryStore:
    """Vercel/Neon 使用的持久化记忆、分析与仪表盘存储。"""

    def __init__(self, database_url: str):
        self.database_url = database_url

    def _connect(self):
        import psycopg
        from psycopg.rows import dict_row

        return psycopg.connect(self.database_url, row_factory=dict_row)

    @staticmethod
    def _json(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, default=str)

    @staticmethod
    def _decoded(value: Any) -> Any:
        if isinstance(value, str):
            return json.loads(value)
        return value

    def add_message(self, *, session_id: str, user_id: str, role: MessageRole,
                    content: str, message_type: str = "text", payload=None,
                    metadata=None, token_count: int = 0) -> MemoryMessage:
        created_at = _utc_now()
        with self._connect() as conn:
            row = conn.execute(
                """INSERT INTO _askdata_messages(
                       session_id,user_id,role,content,message_type,payload_json,
                       metadata_json,token_count,created_at)
                   VALUES (%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s,%s)
                   RETURNING id""",
                (session_id, user_id, role.value, content, message_type,
                 self._json(payload or {}), self._json(metadata or {}), token_count, created_at),
            ).fetchone()
        return MemoryMessage(id=int(row["id"]), session_id=session_id, user_id=user_id,
                             role=role, content=content, message_type=message_type,
                             payload=payload or {}, metadata=metadata or {},
                             token_count=token_count, created_at=created_at)

    def list_messages(self, *, user_id: str, session_id: str) -> List[MemoryMessage]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM _askdata_messages WHERE user_id=%s AND session_id=%s ORDER BY id",
                (user_id, session_id),
            ).fetchall()
        return [self._row_to_message(row) for row in rows]

    def list_sessions(self, *, user_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT session_id, MIN(created_at) created_at, MAX(created_at) updated_at,
                          COUNT(*) message_count,
                          COALESCE((ARRAY_AGG(content ORDER BY id)
                            FILTER (WHERE role='user'))[1], '新会话') title
                   FROM _askdata_messages WHERE user_id=%s
                   GROUP BY session_id ORDER BY MAX(id) DESC LIMIT %s""",
                (user_id, max(1, min(int(limit), 200))),
            ).fetchall()
        return [{**dict(row), "title": str(row["title"])[:60],
                 "message_count": int(row["message_count"])} for row in rows]

    def save_analysis(self, *, user_id: str, session_id: str, run_id: str,
                      title: str, query: str, result: Dict[str, Any]) -> Dict[str, Any]:
        now = _utc_now()
        analysis_id = f"analysis_{uuid.uuid4().hex}"
        with self._connect() as conn:
            row = conn.execute(
                """INSERT INTO _askdata_saved_analyses(
                       id,user_id,session_id,run_id,title,query,result_json,created_at,updated_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s)
                   ON CONFLICT (user_id,run_id) DO UPDATE SET
                     session_id=EXCLUDED.session_id,title=EXCLUDED.title,query=EXCLUDED.query,
                     result_json=EXCLUDED.result_json,updated_at=EXCLUDED.updated_at
                   RETURNING id,created_at""",
                (analysis_id,user_id,session_id,run_id,title,query,self._json(result),now,now),
            ).fetchone()
        return {"id":row["id"],"user_id":user_id,"session_id":session_id,
                "run_id":run_id,"title":title,"query":query,"result":result,
                "created_at":row["created_at"],"updated_at":now}

    def list_saved_analyses(self, *, user_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT id,user_id,session_id,run_id,title,query,created_at,updated_at
                   FROM _askdata_saved_analyses WHERE user_id=%s
                   ORDER BY updated_at DESC LIMIT %s""",
                (user_id, max(1, min(int(limit), 200))),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_saved_analysis(self, *, user_id: str, analysis_id: str) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM _askdata_saved_analyses WHERE user_id=%s AND id=%s",
                (user_id, analysis_id),
            ).fetchone()
        if not row:
            return None
        payload = dict(row)
        payload["result"] = self._decoded(payload.pop("result_json"))
        return payload

    def delete_saved_analysis(self, *, user_id: str, analysis_id: str) -> bool:
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM _askdata_saved_analyses WHERE user_id=%s AND id=%s",
                               (user_id, analysis_id))
        return cur.rowcount > 0

    def create_dashboard(self, *, user_id: str, name: str, description: str = "") -> Dict[str, Any]:
        dashboard_id, now = f"dashboard_{uuid.uuid4().hex}", _utc_now()
        name, description = name.strip()[:80] or "我的仪表盘", description.strip()[:300]
        with self._connect() as conn:
            conn.execute("""INSERT INTO _askdata_dashboards
              (id,user_id,name,description,created_at,updated_at) VALUES (%s,%s,%s,%s,%s,%s)""",
                         (dashboard_id,user_id,name,description,now,now))
        return {"id":dashboard_id,"user_id":user_id,"name":name,"description":description,
                "card_count":0,"created_at":now,"updated_at":now}

    def list_dashboards(self, *, user_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT d.id,d.user_id,d.name,d.description,d.created_at,d.updated_at,
                          COUNT(c.id)::INTEGER card_count
                   FROM _askdata_dashboards d LEFT JOIN _askdata_dashboard_cards c
                     ON c.dashboard_id=d.id WHERE d.user_id=%s GROUP BY d.id
                   ORDER BY d.updated_at DESC LIMIT %s""",
                (user_id, max(1,min(int(limit),100))),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_dashboard(self, *, user_id: str, dashboard_id: str) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            dashboard = conn.execute(
                "SELECT * FROM _askdata_dashboards WHERE user_id=%s AND id=%s",
                (user_id,dashboard_id),
            ).fetchone()
            if not dashboard:
                return None
            rows = conn.execute(
                """SELECT c.id,c.analysis_id,c.title,c.position,c.created_at,a.query,
                          a.result_json,a.updated_at analysis_updated_at
                   FROM _askdata_dashboard_cards c JOIN _askdata_saved_analyses a
                     ON a.id=c.analysis_id
                   WHERE c.dashboard_id=%s AND a.user_id=%s
                   ORDER BY c.position,c.created_at""",
                (dashboard_id,user_id),
            ).fetchall()
        result = dict(dashboard)
        result["cards"] = []
        for row in rows:
            item = dict(row)
            item["result"] = self._decoded(item.pop("result_json"))
            result["cards"].append(item)
        result["card_count"] = len(result["cards"])
        return result

    def add_dashboard_card(self, *, user_id: str, dashboard_id: str,
                           analysis_id: str, title: str = "") -> Optional[Dict[str, Any]]:
        now, card_id = _utc_now(), f"card_{uuid.uuid4().hex}"
        with self._connect() as conn:
            dashboard = conn.execute("SELECT id FROM _askdata_dashboards WHERE user_id=%s AND id=%s",
                                     (user_id,dashboard_id)).fetchone()
            analysis = conn.execute("SELECT id,title FROM _askdata_saved_analyses WHERE user_id=%s AND id=%s",
                                    (user_id,analysis_id)).fetchone()
            if not dashboard or not analysis:
                return None
            existing = conn.execute("""SELECT id,title,position,created_at FROM _askdata_dashboard_cards
                                      WHERE dashboard_id=%s AND analysis_id=%s""",
                                    (dashboard_id,analysis_id)).fetchone()
            if existing:
                return {**dict(existing),"dashboard_id":dashboard_id,"analysis_id":analysis_id}
            row = conn.execute("SELECT COALESCE(MAX(position),-1)+1 position FROM _askdata_dashboard_cards WHERE dashboard_id=%s",
                               (dashboard_id,)).fetchone()
            position, safe_title = int(row["position"]), title.strip()[:120] or analysis["title"]
            conn.execute("""INSERT INTO _askdata_dashboard_cards
              (id,dashboard_id,analysis_id,title,position,created_at) VALUES (%s,%s,%s,%s,%s,%s)""",
                         (card_id,dashboard_id,analysis_id,safe_title,position,now))
            conn.execute("UPDATE _askdata_dashboards SET updated_at=%s WHERE id=%s",(now,dashboard_id))
        return {"id":card_id,"dashboard_id":dashboard_id,"analysis_id":analysis_id,
                "title":safe_title,"position":position,"created_at":now}

    def remove_dashboard_card(self, *, user_id: str, dashboard_id: str, card_id: str) -> bool:
        with self._connect() as conn:
            cur = conn.execute("""DELETE FROM _askdata_dashboard_cards c WHERE c.id=%s AND c.dashboard_id=%s
              AND EXISTS(SELECT 1 FROM _askdata_dashboards d WHERE d.id=c.dashboard_id AND d.user_id=%s)""",
                               (card_id,dashboard_id,user_id))
            if cur.rowcount:
                conn.execute("UPDATE _askdata_dashboards SET updated_at=%s WHERE id=%s AND user_id=%s",
                             (_utc_now(),dashboard_id,user_id))
        return cur.rowcount > 0

    def delete_dashboard(self, *, user_id: str, dashboard_id: str) -> bool:
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM _askdata_dashboards WHERE user_id=%s AND id=%s",
                               (user_id,dashboard_id))
        return cur.rowcount > 0

    def get_latest_summary(self, *, user_id: str, session_id: str) -> Optional[ConversationSummary]:
        with self._connect() as conn:
            row=conn.execute("""SELECT * FROM _askdata_summaries WHERE user_id=%s AND session_id=%s
                              ORDER BY version DESC LIMIT 1""",(user_id,session_id)).fetchone()
        return ConversationSummary(id=int(row["id"]),session_id=row["session_id"],user_id=row["user_id"],
            version=int(row["version"]),content=row["content"],through_message_id=int(row["through_message_id"]),
            created_at=row["created_at"]) if row else None

    def save_summary(self, *, user_id: str, session_id: str, content: str,
                     through_message_id: int) -> ConversationSummary:
        created_at=_utc_now()
        with self._connect() as conn:
            row=conn.execute("""SELECT COALESCE(MAX(version),0)+1 version FROM _askdata_summaries
                              WHERE user_id=%s AND session_id=%s""",(user_id,session_id)).fetchone()
            version=int(row["version"])
            saved=conn.execute("""INSERT INTO _askdata_summaries
              (session_id,user_id,version,content,through_message_id,created_at)
              VALUES (%s,%s,%s,%s,%s,%s) RETURNING id""",
              (session_id,user_id,version,content,through_message_id,created_at)).fetchone()
        return ConversationSummary(id=int(saved["id"]),session_id=session_id,user_id=user_id,
            version=version,content=content,through_message_id=through_message_id,created_at=created_at)

    def save_long_term_memory(self, *, user_id: str, kind: MemoryKind, summary: str,
                              content: Any, metadata: Dict[str,Any], embedding: np.ndarray,
                              memory_id: Optional[str]=None) -> LongTermMemory:
        memory_id, now = memory_id or str(uuid.uuid4()), _utc_now()
        vector=np.asarray(embedding,dtype=np.float32).reshape(-1)
        with self._connect() as conn:
            conn.execute("""INSERT INTO _askdata_long_term_memories
              (id,user_id,kind,summary,content_json,metadata_json,embedding,embedding_dimensions,created_at,updated_at)
              VALUES (%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s,%s,%s,%s)""",
              (memory_id,user_id,kind.value,summary,self._json(content),self._json(metadata),
               vector.tobytes(),int(vector.size),now,now))
        return LongTermMemory(id=memory_id,user_id=user_id,kind=kind,summary=summary,content=content,
                              metadata=metadata,embedding=vector,created_at=now,updated_at=now)

    def list_long_term_memories(self, *, user_id: str,
                                kinds: Optional[Sequence[MemoryKind]]=None) -> List[LongTermMemory]:
        params:list[Any]=[user_id]
        sql="SELECT * FROM _askdata_long_term_memories WHERE user_id=%s"
        if kinds:
            sql += " AND kind = ANY(%s)"
            params.append([kind.value for kind in kinds])
        sql += " ORDER BY created_at DESC"
        with self._connect() as conn: rows=conn.execute(sql,params).fetchall()
        return [self._row_to_long_term(row) for row in rows]

    def get_long_term_memory(self, *, user_id: str, memory_id: str) -> Optional[LongTermMemory]:
        with self._connect() as conn:
            row=conn.execute("SELECT * FROM _askdata_long_term_memories WHERE user_id=%s AND id=%s",
                             (user_id,memory_id)).fetchone()
        return self._row_to_long_term(row) if row else None

    def delete_long_term_memory(self, *, user_id: str, memory_id: str) -> bool:
        with self._connect() as conn:
            cur=conn.execute("DELETE FROM _askdata_long_term_memories WHERE user_id=%s AND id=%s",
                             (user_id,memory_id))
        return cur.rowcount>0

    def _row_to_message(self,row) -> MemoryMessage:
        return MemoryMessage(id=int(row["id"]),session_id=row["session_id"],user_id=row["user_id"],
            role=MessageRole(row["role"]),content=row["content"],message_type=row["message_type"],
            payload=self._decoded(row["payload_json"]),metadata=self._decoded(row["metadata_json"]),
            token_count=int(row["token_count"]),created_at=row["created_at"])

    def _row_to_long_term(self,row) -> LongTermMemory:
        vector=np.frombuffer(bytes(row["embedding"]),dtype=np.float32,count=int(row["embedding_dimensions"])).copy()
        return LongTermMemory(id=row["id"],user_id=row["user_id"],kind=MemoryKind(row["kind"]),
            summary=row["summary"],content=self._decoded(row["content_json"]),
            metadata=self._decoded(row["metadata_json"]),embedding=vector,
            created_at=row["created_at"],updated_at=row["updated_at"])
