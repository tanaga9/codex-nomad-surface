from __future__ import annotations

from typing import Any


TURN_RUN_STARTING = "starting"
TURN_RUN_RUNNING = "running"
TURN_RUN_AWAITING_APPROVAL = "awaiting_approval"
TURN_RUN_RESPONDING_APPROVAL = "responding_approval"
TURN_RUN_COMPLETED = "completed"
TURN_RUN_FAILED = "failed"


def turn_run_can_start_worker(run: dict[str, Any]) -> bool:
    return bool(
        run.get("status") == TURN_RUN_STARTING
        and not run.get("worker_id")
        and not run.get("result")
        and not run.get("approval")
    )
