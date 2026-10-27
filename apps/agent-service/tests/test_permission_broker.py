import asyncio

import pytest

from desktopmate_agent.tools.permissions import PermissionDecision, PermissionRequestBroker


@pytest.mark.asyncio
async def test_broker_resolves_approval():
    broker = PermissionRequestBroker(timeout_seconds=1)
    request = broker.create_request(
        session_id="s1",
        run_id="r1",
        risk_level="write",
        summary="准备移动文件",
        payload={"path": "a.md"},
    )

    waiter = asyncio.create_task(broker.wait_for_decision(request.request_id))
    broker.resolve(request.request_id, PermissionDecision(decision="approved"))

    decision = await waiter
    assert decision.decision == "approved"


@pytest.mark.asyncio
async def test_broker_times_out_as_denied():
    broker = PermissionRequestBroker(timeout_seconds=0.01)
    request = broker.create_request(
        session_id="s1",
        run_id="r1",
        risk_level="write",
        summary="准备移动文件",
        payload={},
    )

    decision = await broker.wait_for_decision(request.request_id)

    assert decision.decision == "denied"
    assert decision.reason == "permission request timed out"
