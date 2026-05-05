import unittest

from codex_nomad_surface.turn_run import (
    TURN_RUN_AWAITING_APPROVAL,
    TURN_RUN_COMPLETED,
    TURN_RUN_RUNNING,
    TURN_RUN_STARTING,
    turn_run_can_start_worker,
)


class TurnRunStateTests(unittest.TestCase):
    def test_worker_starts_only_from_fresh_starting_state(self) -> None:
        self.assertTrue(turn_run_can_start_worker({"status": TURN_RUN_STARTING}))

        for status in {
            TURN_RUN_RUNNING,
            TURN_RUN_AWAITING_APPROVAL,
            TURN_RUN_COMPLETED,
        }:
            self.assertFalse(turn_run_can_start_worker({"status": status}))

    def test_existing_worker_or_terminal_state_prevents_restart(self) -> None:
        self.assertFalse(
            turn_run_can_start_worker(
                {"status": TURN_RUN_STARTING, "worker_id": "worker-1"}
            )
        )
        self.assertFalse(
            turn_run_can_start_worker(
                {"status": TURN_RUN_STARTING, "result": {"ok": True}}
            )
        )
        self.assertFalse(
            turn_run_can_start_worker(
                {"status": TURN_RUN_STARTING, "approval": {"id": "approval-1"}}
            )
        )


if __name__ == "__main__":
    unittest.main()
