import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field


class PermissionRequest(BaseModel):
    request_id: str
    session_id: str
    run_id: str
    risk_level: str
    summary: str
    payload: dict[str, Any] = Field(default_factory=dict)
    expires_at: datetime


class PermissionDecision(BaseModel):
    decision: Literal["approved", "denied"]
    reason: str | None = None


class _PendingRequest:
    def __init__(self, request: PermissionRequest) -> None:
        self.request = request
        self.future: asyncio.Future[PermissionDecision] = asyncio.get_running_loop().create_future()


class PermissionRequestBroker:
    def __init__(self, timeout_seconds: float = 60.0) -> None:
        self.timeout_seconds = timeout_seconds
        self._pending: dict[str, _PendingRequest] = {}

    def create_request(
        self,
        *,
        session_id: str,
        run_id: str,
        risk_level: str,
        summary: str,
        payload: dict[str, Any],
    ) -> PermissionRequest:
        request = PermissionRequest(
            request_id=str(uuid4()),
            session_id=session_id,
            run_id=run_id,
            risk_level=risk_level,
            summary=summary,
            payload=payload,
            expires_at=datetime.now(UTC) + timedelta(seconds=self.timeout_seconds),
        )
        self._pending[request.request_id] = _PendingRequest(request)
        return request

    def pending_count(self) -> int:
        """当前待审批的权限请求数（供 /api/runtime/status 使用）。"""
        return len(self._pending)

    async def wait_for_decision(self, request_id: str) -> PermissionDecision:
        pending = self._pending[request_id]
        try:
            return await asyncio.wait_for(pending.future, timeout=self.timeout_seconds)
        except TimeoutError:
            decision = PermissionDecision(decision="denied", reason="permission request timed out")
            if not pending.future.done():
                pending.future.set_result(decision)
            return decision
        finally:
            self._pending.pop(request_id, None)

    def resolve(self, request_id: str, decision: PermissionDecision) -> None:
        pending = self._pending.get(request_id)
        if pending is None:
            raise KeyError(f"Permission request '{request_id}' is not pending")
        if not pending.future.done():
            pending.future.set_result(decision)
