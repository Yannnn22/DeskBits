"""Runtime Event Bus：给悬浮桌宠提供"当前状态"与提醒事件的实时订阅。

- 事件追加：内存环形缓冲（保留最近 ``max_events`` 条）+ JSONL 持久化。
- 订阅/推送：``WebSocket /ws/runtime`` 通过 ``subscribe`` 接收实时事件。
- 状态快照：``snapshot()`` 供 ``GET /api/runtime/status`` 使用。
- 不替代 run trace：trace 是逐 run 的调试记录，这里只承载"当前状态"。

事件类型（S2 先实现 run 相关，todo_due / reminder_due / notification
由 S3 提醒服务后续写入）：
``runtime_status`` / ``run_started`` / ``run_progress`` / ``run_completed`` /
``run_failed``。
"""

import asyncio
import json
from collections import deque
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Awaitable, Callable

Subscriber = Callable[[dict[str, Any]], Awaitable[None]]


def event_id(seq: int) -> str:
    return f"evt_{seq:08d}"


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


class RuntimeEventBus:
    """进程内事件总线 + JSONL 持久化。

    - 只保留最近 ``max_events`` 条可查询事件（cursor 语义单调递增）。
    - 磁盘文件超过 ``max_events * 10`` 行时自动压缩为当前缓冲。
    - 订阅者收到的是 ``{"id","type","created_at","payload"}`` 完整事件 dict。
    """

    def __init__(self, path: Path | None = None, max_events: int = 1000) -> None:
        self.path = path
        self.max_events = max_events
        self._events: deque[dict[str, Any]] = deque(maxlen=max_events)
        self._subscribers: dict[int, Subscriber] = {}
        self._next_sub_id = 1
        self._next_seq = 1
        self._last_run_status: str | None = None
        self._last_message: str | None = None
        if path is not None:
            self._load()

    # --- 写入 ---

    async def append(self, type_: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        seq = self._next_seq
        self._next_seq += 1
        event = {
            "id": event_id(seq),
            "type": type_,
            "created_at": _now_iso(),
            "payload": payload or {},
        }
        self._events.append(event)
        self._track_status(type_, payload or {})
        if self.path is not None:
            self._append_line(seq, event)
            self._maybe_compact()
        for sub_id in list(self._subscribers):
            try:
                await asyncio.wait_for(self._subscribers[sub_id](event), timeout=5.0)
            except Exception:
                # 订阅者异常或推送超时（慢/卡住的 WebSocket）：移除，避免阻塞后续事件
                self._subscribers.pop(sub_id, None)
        return event

    def _track_status(self, type_: str, payload: dict[str, Any]) -> None:
        if type_ == "run_started":
            self._last_run_status = "running"
        elif type_ == "run_completed":
            self._last_run_status = "completed"
        elif type_ == "run_failed":
            self._last_run_status = "failed"
        elif type_ == "run_progress":
            text = payload.get("text")
            tool = payload.get("tool")
            if text:
                self._last_message = text
            elif tool:
                self._last_message = f"正在执行 {tool}"

    # --- 读取 ---

    def events_since(self, cursor: int = 0, limit: int = 100) -> tuple[list[dict[str, Any]], int]:
        """返回 seq > cursor 的事件（最多 limit 条）与最新已见 seq。"""
        items = [event for event in self._events if _seq_of(event) > cursor]
        items = items[: max(1, min(limit, 1000))]
        latest_seq = items[-1]["id"] if items else self._latest_seq()
        return items, _seq_of_id(latest_seq)

    def event_count(self) -> int:
        """当前内存中可查询的事件条数（最近 max_events 条）。"""
        return len(self._events)

    def _latest_seq(self) -> int:
        if self._events:
            return _seq_of(self._events[-1])
        return max(0, self._next_seq - 1)

    def snapshot(
        self,
        *,
        active_runs: int = 0,
        pending_permissions: int = 0,
        due_reminders: int = 0,
    ) -> dict[str, Any]:
        return {
            "active_runs": active_runs,
            "last_run_status": self._last_run_status,
            "last_message": self._last_message,
            "pending_permissions": pending_permissions,
            "due_reminders": due_reminders,
        }

    # --- 订阅 ---

    def subscribe(self, subscriber: Subscriber) -> int:
        sub_id = self._next_sub_id
        self._next_sub_id += 1
        self._subscribers[sub_id] = subscriber
        return sub_id

    def unsubscribe(self, sub_id: int) -> bool:
        return self._subscribers.pop(sub_id, None) is not None

    def subscriber_count(self) -> int:
        return len(self._subscribers)

    # --- 持久化 ---

    def _load(self) -> None:
        if self.path is None or not self.path.exists():
            return
        max_seq = 0
        try:
            for line in self.path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                raw = json.loads(line)
                seq = int(raw.get("seq", 0))
                max_seq = max(max_seq, seq)
                self._events.append(
                    {
                        "id": event_id(seq),
                        "type": raw.get("type", ""),
                        "created_at": raw.get("created_at", ""),
                        "payload": raw.get("payload") or {},
                    }
                )
                self._track_status(raw.get("type", ""), raw.get("payload") or {})
        except (OSError, json.JSONDecodeError, ValueError):
            return
        self._next_seq = max_seq + 1

    def _append_line(self, seq: int, event: dict[str, Any]) -> None:
        assert self.path is not None
        self.path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(
            {
                "seq": seq,
                "type": event["type"],
                "created_at": event["created_at"],
                "payload": event["payload"],
            },
            ensure_ascii=False,
        )
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")

    def _maybe_compact(self) -> None:
        """磁盘文件超过阈值时压缩为当前内存缓冲（最近 max_events 条）。"""
        assert self.path is not None
        if self.path.stat().st_size < self.max_events * 10 * 256:
            return
        with self.path.open("w", encoding="utf-8") as handle:
            for event in self._events:
                seq = _seq_of(event)
                handle.write(
                    json.dumps(
                        {
                            "seq": seq,
                            "type": event["type"],
                            "created_at": event["created_at"],
                            "payload": event["payload"],
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )


def _seq_of(event: dict[str, Any]) -> int:
    return _seq_of_id(event.get("id", "evt_00000000"))


def _seq_of_id(event_id_value: str) -> int:
    if isinstance(event_id_value, int):
        return event_id_value
    try:
        return int(str(event_id_value).removeprefix("evt_"))
    except ValueError:
        return 0
