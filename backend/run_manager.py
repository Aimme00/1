from __future__ import annotations

import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple


TERMINAL_STATUSES = {"completed", "failed", "cancelled"}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class RunNotFoundError(KeyError):
    pass


@dataclass
class RunEvent:
    sequence: int
    event: str
    node: str
    status: str
    message: str
    data: Dict[str, Any]
    created_at: str = field(default_factory=_utc_now)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "sequence": self.sequence,
            "event": self.event,
            "node": self.node,
            "status": self.status,
            "message": self.message,
            "data": self.data,
            "created_at": self.created_at,
        }


@dataclass
class RunRecord:
    run_id: str
    user_id: str
    session_id: str
    query: str
    status: str = "pending"
    result: Optional[Dict[str, Any]] = None
    error: Optional[Dict[str, Any]] = None
    events: List[RunEvent] = field(default_factory=list)
    created_at: str = field(default_factory=_utc_now)
    updated_at: str = field(default_factory=_utc_now)
    cancel_event: threading.Event = field(default_factory=threading.Event, repr=False)

    def to_dict(self, include_result: bool = True) -> Dict[str, Any]:
        payload = {
            "run_id": self.run_id,
            "user_id": self.user_id,
            "session_id": self.session_id,
            "query": self.query,
            "status": self.status,
            "error": self.error,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "event_count": len(self.events),
        }
        if include_result:
            payload["result"] = self.result
        return payload


RunHandler = Callable[
    [RunRecord, Callable[[str, str, str, Optional[Dict[str, Any]]], None], Callable[[], bool]],
    Dict[str, Any],
]


class RunManager:
    """线程安全的后台运行管理器，支持 SSE 事件回放与取消。"""

    def __init__(self, max_workers: int = 4):
        self._records: Dict[str, RunRecord] = {}
        self._condition = threading.Condition()
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="askdata-run")

    def submit(
        self,
        *,
        user_id: str,
        session_id: str,
        query: str,
        handler: RunHandler,
    ) -> RunRecord:
        record = RunRecord(
            run_id=f"run_{uuid.uuid4().hex}",
            user_id=user_id,
            session_id=session_id,
            query=query,
        )
        with self._condition:
            self._records[record.run_id] = record
            self._append_event(record, "run", "queued", "pending", "任务已进入队列", {})
        self._executor.submit(self._execute, record.run_id, handler)
        return record

    def get(self, run_id: str) -> RunRecord:
        with self._condition:
            record = self._records.get(run_id)
            if record is None:
                raise RunNotFoundError(run_id)
            return record

    def snapshot(self, run_id: str, include_result: bool = True) -> Dict[str, Any]:
        with self._condition:
            return self.get(run_id).to_dict(include_result=include_result)

    def emit(
        self,
        run_id: str,
        node: str,
        status: str,
        message: str,
        data: Optional[Dict[str, Any]] = None,
    ) -> None:
        with self._condition:
            record = self.get(run_id)
            self._append_event(record, node, "progress", status, message, data or {})

    def cancel(self, run_id: str) -> Dict[str, Any]:
        with self._condition:
            record = self.get(run_id)
            if record.status in TERMINAL_STATUSES:
                return record.to_dict()
            record.cancel_event.set()
            record.updated_at = _utc_now()
            self._append_event(record, "run", "cancel_requested", "running", "已请求取消任务", {})
            return record.to_dict()

    def events_after(
        self,
        run_id: str,
        after: int = 0,
        timeout: float = 15.0,
    ) -> Tuple[List[Dict[str, Any]], bool]:
        with self._condition:
            record = self.get(run_id)
            has_new = any(event.sequence > after for event in record.events)
            if not has_new and record.status not in TERMINAL_STATUSES:
                self._condition.wait_for(
                    lambda: any(event.sequence > after for event in record.events)
                    or record.status in TERMINAL_STATUSES,
                    timeout=timeout,
                )
            events = [event.to_dict() for event in record.events if event.sequence > after]
            return events, record.status in TERMINAL_STATUSES

    def shutdown(self, wait: bool = True) -> None:
        self._executor.shutdown(wait=wait, cancel_futures=False)

    def _execute(self, run_id: str, handler: RunHandler) -> None:
        with self._condition:
            record = self.get(run_id)
            if record.cancel_event.is_set():
                self._finish_cancelled(record)
                return
            record.status = "running"
            record.updated_at = _utc_now()
            self._append_event(record, "run", "started", "running", "任务开始执行", {})

        def emit(node: str, status: str, message: str, data=None) -> None:
            self.emit(run_id, node, status, message, data)

        try:
            result = handler(record, emit, record.cancel_event.is_set)
            with self._condition:
                record = self.get(run_id)
                result_status = str((result or {}).get("status") or "completed")
                if record.cancel_event.is_set() or result_status == "cancelled":
                    record.result = result
                    self._finish_cancelled(record)
                elif result_status == "failed":
                    record.result = result
                    record.status = "failed"
                    record.error = (result or {}).get("error") or {
                        "code": "agent_failed",
                        "message": "Agent 执行失败",
                    }
                    record.updated_at = _utc_now()
                    self._append_event(record, "run", "failed", "failed", record.error["message"], {})
                else:
                    record.result = result
                    record.status = "completed"
                    record.updated_at = _utc_now()
                    self._append_event(record, "run", "completed", "completed", "任务完成", {})
        except Exception as exc:
            with self._condition:
                record = self.get(run_id)
                record.status = "failed"
                record.error = {"code": "run_handler_error", "message": str(exc)}
                record.updated_at = _utc_now()
                self._append_event(record, "run", "failed", "failed", str(exc), {})

    def _finish_cancelled(self, record: RunRecord) -> None:
        record.status = "cancelled"
        record.updated_at = _utc_now()
        self._append_event(record, "run", "cancelled", "cancelled", "任务已取消", {})

    def _append_event(
        self,
        record: RunRecord,
        node: str,
        event: str,
        status: str,
        message: str,
        data: Dict[str, Any],
    ) -> None:
        record.events.append(
            RunEvent(
                sequence=len(record.events) + 1,
                event=event,
                node=node,
                status=status,
                message=message,
                data=data,
            )
        )
        record.updated_at = _utc_now()
        self._condition.notify_all()
