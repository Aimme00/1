from __future__ import annotations

import json
import threading
import uuid
from typing import Any, Dict, List, Tuple

from .run_manager import RunEvent, RunManager, RunNotFoundError, RunRecord, _utc_now


class PostgresRunManager(RunManager):
    """Vercel 运行管理器：请求内同步执行，结果跨实例持久化。"""

    def __init__(self, database_url: str):
        self.database_url = database_url
        self._condition = threading.Condition()

    def _connect(self):
        import psycopg
        from psycopg.rows import dict_row

        return psycopg.connect(self.database_url, row_factory=dict_row)

    @staticmethod
    def _json(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, default=str)

    @staticmethod
    def _decode(value: Any) -> Any:
        return json.loads(value) if isinstance(value, str) else value

    def submit(self, *, user_id: str, session_id: str, query: str, handler) -> RunRecord:
        record = RunRecord(
            run_id=f"run_{uuid.uuid4().hex}",
            user_id=user_id,
            session_id=session_id,
            query=query,
        )
        self._append_event(record, "run", "queued", "pending", "任务已进入队列", {})
        record.status = "running"
        self._append_event(record, "run", "started", "running", "任务开始执行", {})

        def emit(node: str, status: str, message: str, data=None):
            self._append_event(record, node, "progress", status, message, data or {})

        try:
            result = handler(record, emit, record.cancel_event.is_set)
            result_status = str((result or {}).get("status") or "completed")
            record.result = result
            if result_status == "failed":
                record.status = "failed"
                record.error = (result or {}).get("error") or {
                    "code": "agent_failed", "message": "Agent 执行失败"
                }
                self._append_event(record, "run", "failed", "failed", record.error["message"], {})
            elif result_status == "cancelled":
                record.status = "cancelled"
                self._append_event(record, "run", "cancelled", "cancelled", "任务已取消", {})
            else:
                record.status = "completed"
                self._append_event(record, "run", "completed", "completed", "任务完成", {})
        except Exception as exc:
            record.status = "failed"
            record.error = {"code": "run_handler_error", "message": str(exc)[:500]}
            self._append_event(record, "run", "failed", "failed", record.error["message"], {})
        self._save(record)
        return record

    def get(self, run_id: str) -> RunRecord:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM _askdata_runs WHERE run_id=%s", (run_id,)
            ).fetchone()
        if not row:
            raise RunNotFoundError(run_id)
        events = [RunEvent(**event) for event in self._decode(row["events_json"])]
        return RunRecord(
            run_id=row["run_id"], user_id=row["user_id"], session_id=row["session_id"],
            query=row["query"], status=row["status"],
            result=self._decode(row["result_json"]) if row["result_json"] is not None else None,
            error=self._decode(row["error_json"]) if row["error_json"] is not None else None,
            events=events, created_at=row["created_at"], updated_at=row["updated_at"],
        )

    def snapshot(self, run_id: str, include_result: bool = True) -> Dict[str, Any]:
        return self.get(run_id).to_dict(include_result=include_result)

    def events_after(self, run_id: str, after: int = 0,
                     timeout: float = 15.0) -> Tuple[List[Dict[str, Any]], bool]:
        del timeout
        record = self.get(run_id)
        return ([event.to_dict() for event in record.events if event.sequence > after],
                record.status in {"completed", "failed", "cancelled"})

    def cancel(self, run_id: str) -> Dict[str, Any]:
        record = self.get(run_id)
        return record.to_dict()

    def shutdown(self, wait: bool = True) -> None:
        del wait

    @staticmethod
    def _append_event(record: RunRecord, node: str, event: str, status: str,
                      message: str, data: Dict[str, Any]) -> None:
        record.events.append(RunEvent(sequence=len(record.events)+1,event=event,node=node,
                                      status=status,message=message,data=data))
        record.updated_at = _utc_now()

    def _save(self, record: RunRecord) -> None:
        events = [event.to_dict() for event in record.events]
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO _askdata_runs(
                       run_id,user_id,session_id,query,status,result_json,error_json,
                       events_json,created_at,updated_at)
                   VALUES (%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s::jsonb,%s,%s)
                   ON CONFLICT(run_id) DO UPDATE SET status=EXCLUDED.status,
                     result_json=EXCLUDED.result_json,error_json=EXCLUDED.error_json,
                     events_json=EXCLUDED.events_json,updated_at=EXCLUDED.updated_at""",
                (record.run_id,record.user_id,record.session_id,record.query,record.status,
                 self._json(record.result) if record.result is not None else None,
                 self._json(record.error) if record.error is not None else None,
                 self._json(events),record.created_at,record.updated_at),
            )
