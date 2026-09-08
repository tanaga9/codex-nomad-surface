from pathlib import Path
import shutil
import subprocess

import pytest


def test_chat_input_drag_event_lifecycle() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required for browser event unit tests")
    result = subprocess.run(
        [node, "--test", "tests/js/chat_input_drag_guard.test.cjs"],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
