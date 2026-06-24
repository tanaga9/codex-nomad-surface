import pytest

from codex_nomad_surface.turn_run import (
    TURN_RUN_AWAITING_APPROVAL,
    TURN_RUN_COMPLETED,
    TURN_RUN_RUNNING,
    TURN_RUN_STARTING,
    turn_run_can_start_worker,
)


def test_worker_starts_from_fresh_starting_state() -> None:
    assert turn_run_can_start_worker({"status": TURN_RUN_STARTING})


@pytest.mark.parametrize(
    "status",
    [
        TURN_RUN_RUNNING,
        TURN_RUN_AWAITING_APPROVAL,
        TURN_RUN_COMPLETED,
    ],
)
def test_terminal_or_active_status_prevents_worker_start(status: str) -> None:
    assert not turn_run_can_start_worker({"status": status})


@pytest.mark.parametrize(
    "run",
    [
        {"status": TURN_RUN_STARTING, "worker_id": "worker-1"},
        {"status": TURN_RUN_STARTING, "result": {"ok": True}},
        {"status": TURN_RUN_STARTING, "approval": {"id": "approval-1"}},
    ],
)
def test_existing_worker_or_result_prevents_restart(run: dict) -> None:
    assert not turn_run_can_start_worker(run)
