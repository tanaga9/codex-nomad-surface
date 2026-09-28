from pathlib import Path
import shutil
import subprocess

import pytest


def test_template_delivery_lifecycle():
    node = shutil.which('node')
    if node is None:
        pytest.skip('Node.js is required for delivery lifecycle tests')
    result = subprocess.run(
        [node, '--test', 'tests/js/template_delivery.test.cjs'],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
