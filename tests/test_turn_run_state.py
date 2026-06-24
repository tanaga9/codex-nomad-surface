import pytest

from codex_nomad_surface.turn_run import (
    TURN_RUN_AWAITING_APPROVAL,
    TURN_RUN_COMPLETED,
    TURN_RUN_RUNNING,
    TURN_RUN_STARTING,
    turn_run_can_start_worker,
)
from codex_nomad_surface.app import chat_thread_is_active, thread_status_is_active
from codex_nomad_surface.chat_store import ChatSession
from codex_nomad_surface.codex_client import CodexThread


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


@pytest.mark.parametrize(
    "status",
    ["inProgress", "in-progress", "in_progress"],
)
def test_thread_status_is_active_for_progress_states(status: str) -> None:
    assert thread_status_is_active(status)


@pytest.mark.parametrize(
    "status",
    ["", "active", "busy", "idle", "completed", "failed", "pending", "queued", "running"],
)
def test_thread_status_is_not_active_for_terminal_or_empty_states(status: str) -> None:
    assert not thread_status_is_active(status)


def test_chat_thread_is_active_matches_selected_server_thread() -> None:
    chat = ChatSession(
        id="thread:thread-1",
        project_path="/path/to/project",
        title="Thread",
        thread_id="thread-1",
    )
    threads = [
        CodexThread(
            id="thread-1",
            preview="Thread",
            cwd="/path/to/project",
            status="inProgress",
        )
    ]

    assert chat_thread_is_active(chat, threads)


def test_chat_thread_is_active_ignores_other_threads() -> None:
    chat = ChatSession(
        id="thread:thread-1",
        project_path="/path/to/project",
        title="Thread",
        thread_id="thread-1",
    )
    threads = [
        CodexThread(
            id="thread-2",
            preview="Other",
            cwd="/path/to/project",
            status="inProgress",
        )
    ]

    assert not chat_thread_is_active(chat, threads)
