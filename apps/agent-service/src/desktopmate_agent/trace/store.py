import json
from datetime import UTC, datetime
from pathlib import Path

from desktopmate_agent.trace.events import TraceEvent


class JsonlTraceStore:
    def __init__(self, trace_dir: Path, retention_days: int | None = None) -> None:
        self.trace_dir = trace_dir
        self.trace_dir.mkdir(parents=True, exist_ok=True)
        self.retention_days = retention_days
        self._append_count = 0
        self.prune()

    def append(self, event: TraceEvent) -> None:
        path = self._path_for_run(event.run_id)
        with path.open("a", encoding="utf-8") as file:
            file.write(event.model_dump_json() + "\n")
        self._append_count += 1
        if self._append_count % 100 == 0:
            self.prune()

    def prune(self) -> int:
        if self.retention_days is None:
            return 0
        cutoff = datetime.now(UTC).timestamp() - self.retention_days * 86_400
        removed = 0
        for path in self.trace_dir.glob("run_*.jsonl"):
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink()
                    removed += 1
            except FileNotFoundError:
                continue
        return removed

    def read_run(self, run_id: str) -> list[TraceEvent]:
        path = self._path_for_run(run_id)
        if not path.exists():
            return []
        events: list[TraceEvent] = []
        with path.open("r", encoding="utf-8") as file:
            for line in file:
                if line.strip():
                    events.append(TraceEvent.model_validate(json.loads(line)))
        return events

    def has_run(self, run_id: str) -> bool:
        return self._path_for_run(run_id).exists()

    def list_run_ids(self, session_id: str | None = None) -> list[str]:
        run_ids: list[str] = []
        for path in self.trace_dir.glob("run_*.jsonl"):
            run_id = path.stem
            if session_id is None or self._first_event_session(path) == session_id:
                run_ids.append(run_id)
        return sorted(run_ids, reverse=True)

    def query_events(
        self,
        *,
        session_id: str | None = None,
        run_id: str | None = None,
        event_type: str | None = None,
    ) -> list[TraceEvent]:
        run_ids = [run_id] if run_id is not None else self.list_run_ids(session_id)
        events: list[TraceEvent] = []
        for active_run_id in run_ids:
            for event in self.read_run(active_run_id):
                if session_id is not None and event.session_id != session_id:
                    continue
                if event_type is not None and event.type != event_type:
                    continue
                events.append(event)
        events.sort(key=lambda event: event.created_at)
        return events

    def _first_event_session(self, path: Path) -> str | None:
        try:
            with path.open("r", encoding="utf-8") as file:
                first_line = file.readline().strip()
        except OSError:
            return None
        if not first_line:
            return None
        try:
            data = json.loads(first_line)
        except json.JSONDecodeError:
            return None
        session_id = data.get("session_id")
        return str(session_id) if session_id else None

    def _path_for_run(self, run_id: str) -> Path:
        safe_run_id = run_id.replace("/", "_")
        return self.trace_dir / f"{safe_run_id}.jsonl"
