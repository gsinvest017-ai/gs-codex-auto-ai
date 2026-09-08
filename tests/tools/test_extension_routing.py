"""Run the real extension modules against mocked VS Code/process boundaries."""
import shutil
import subprocess
from pathlib import Path

import pytest


def test_extension_routing_integration():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js unavailable")
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [node, "--test", "vscode-extension/routing.test.js"], cwd=root,
        capture_output=True, text=True, encoding="utf-8", timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
