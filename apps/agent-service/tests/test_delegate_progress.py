import asyncio

from desktopmate_agent.tools.delegate_tools import DelegateBatchArgs, DelegateBatchTool, SubTask


async def fake_runner(goal: str, context: str) -> str:
    await asyncio.sleep(0.01)
    return f"[报告] {goal}"


def test_delegate_batch_progress_callback_called_for_three_subtasks():
    events = []

    async def callback(payload: dict) -> None:
        events.append(payload)

    tool = DelegateBatchTool(runner=fake_runner, progress_callback=callback)
    result = asyncio.run(
        tool.run(
            DelegateBatchArgs(
                subtasks=[
                    SubTask(goal="任务A"),
                    SubTask(goal="任务B"),
                    SubTask(goal="任务C"),
                ]
            )
        )
    )

    assert result.status == "success"
    assert {event["goal"] for event in events} == {"任务A", "任务B", "任务C"}
    assert [event["status"] for event in events].count("running") == 3
    assert [event["status"] for event in events].count("completed") == 3


def test_delegate_batch_progress_callback_none_is_noop():
    tool = DelegateBatchTool(runner=fake_runner, progress_callback=None)

    result = asyncio.run(
        tool.run(DelegateBatchArgs(subtasks=[SubTask(goal="任务A"), SubTask(goal="任务B")]))
    )

    assert result.status == "success"
    assert len(result.data["results"]) == 2
