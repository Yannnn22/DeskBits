"""运行任务注册表，支持按 run_id 取消正在执行的任务。"""

import asyncio
from typing import Any


class RunRegistry:
    def __init__(self) -> None:
        self._runs: dict[str, asyncio.Task[Any]] = {}

    def register(self, run_id: str, task: asyncio.Task[Any]) -> None:
        self._runs[run_id] = task

    def unregister(self, run_id: str) -> None:
        self._runs.pop(run_id, None)

    def cancel(self, run_id: str) -> bool:
        task = self._runs.get(run_id)
        if task is None or task.done():
            return False
        task.cancel()
        return True

    def active_run_ids(self) -> list[str]:
        return [run_id for run_id, task in self._runs.items() if not task.done()]
