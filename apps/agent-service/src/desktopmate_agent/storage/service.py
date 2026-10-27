"""S6 存储统计与清理。

- ``usage()``：统计数据库/上传/知识库/trace 的磁盘占用。
- ``cleanup()``：按目标清理。
  - ``traces``：删除早于 ``older_than_days`` 的 run_*.jsonl（保留近期/当前会话）。
  - ``orphan_uploads``：删除不再被任何消息 metadata 引用的附件。
  - knowledge originals 不自动删（须显式删除文档）。
- 每次 cleanup 通过 ``storage_cleanup`` runtime 事件留痕。
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from desktopmate_agent.attachments.store import AttachmentStore
from desktopmate_agent.config import Settings
from desktopmate_agent.runtime.events import RuntimeEventBus
from desktopmate_agent.trace.store import JsonlTraceStore

VALID_TARGETS = {"traces", "orphan_uploads"}


class StorageService:
    def __init__(
        self,
        *,
        settings: Settings,
        attachment_store: AttachmentStore,
        trace_store: JsonlTraceStore | None = None,
        runtime_events: RuntimeEventBus | None = None,
    ) -> None:
        self.settings = settings
        self.attachment_store = attachment_store
        self.trace_store = trace_store
        self.runtime_events = runtime_events

    # --- 统计 ---

    @staticmethod
    def _dir_bytes(path: Path) -> int:
        if not path.exists():
            return 0
        return sum(file.stat().st_size for file in path.rglob("*") if file.is_file())

    def usage(self) -> dict[str, Any]:
        db_path = self.settings.knowledge_db_path
        database_bytes = db_path.stat().st_size if db_path.exists() else 0
        session_db = self.settings.session_db_path
        if session_db != db_path and session_db.exists():
            database_bytes += session_db.stat().st_size
        return {
            "data_dir": str(self.settings.app_data_dir),
            "database_bytes": database_bytes,
            "uploads_bytes": self._dir_bytes(self.settings.uploads_dir),
            "knowledge_bytes": self._dir_bytes(self.settings.knowledge_dir),
            "traces_bytes": self._dir_bytes(self.settings.trace_dir),
        }

    # --- 清理 ---

    def _referenced_attachment_ids(self) -> set[str]:
        """扫描所有消息 metadata，收集仍被引用的附件 id。"""
        referenced: set[str] = set()
        db_path = self.settings.session_db_path
        if not db_path.exists():
            return referenced
        connection = sqlite3.connect(db_path)
        try:
            rows = connection.execute(
                "SELECT metadata FROM messages WHERE metadata IS NOT NULL"
            ).fetchall()
        finally:
            connection.close()
        for (metadata_raw,) in rows:
            try:
                metadata = json.loads(metadata_raw) if metadata_raw else {}
            except json.JSONDecodeError:
                continue
            for attachment_id in metadata.get("attachments", []):
                if isinstance(attachment_id, str):
                    referenced.add(attachment_id)
        return referenced

    def orphan_upload_ids(self) -> list[str]:
        referenced = self._referenced_attachment_ids()
        return [aid for aid in self.attachment_store.list_ids() if aid not in referenced]

    def _prune_traces(self, older_than_days: int) -> int:
        cutoff = datetime.now(UTC).timestamp() - older_than_days * 86_400
        removed = 0
        for path in self.settings.trace_dir.glob("run_*.jsonl"):
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink()
                    removed += 1
            except FileNotFoundError:
                continue
        return removed

    async def cleanup(
        self,
        *,
        targets: list[str],
        older_than_days: int = 30,
    ) -> dict[str, Any]:
        invalid = [target for target in targets if target not in VALID_TARGETS]
        if invalid:
            raise ValueError(f"无效的清理目标: {', '.join(invalid)}（支持: {', '.join(sorted(VALID_TARGETS))}）")

        result: dict[str, Any] = {
            "targets": targets,
            "older_than_days": older_than_days,
            "traces_removed": 0,
            "orphan_uploads_removed": 0,
        }
        if "traces" in targets:
            result["traces_removed"] = self._prune_traces(older_than_days)
        if "orphan_uploads" in targets:
            orphan_ids = self.orphan_upload_ids()
            result["orphan_uploads_removed"] = sum(
                1 for aid in orphan_ids if self.attachment_store.delete(aid)
            )

        if self.runtime_events is not None:
            await self.runtime_events.append(
                "storage_cleanup",
                {
                    "targets": targets,
                    "older_than_days": older_than_days,
                    "traces_removed": result["traces_removed"],
                    "orphan_uploads_removed": result["orphan_uploads_removed"],
                },
            )
        return result
