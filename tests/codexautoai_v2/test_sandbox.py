"""Tests for the SAFE-R1 / SECGOV-R4 execution sandbox."""
import os
import sys
import time
from pathlib import Path

import pytest

from src.codexautoai_v2.sandbox import (
    JobLimits,
    Sandbox,
    SandboxResult,
    SandboxViolation,
    is_within,
)


def test_is_within(tmp_path):
    assert is_within(tmp_path / "a.txt", tmp_path)
    assert is_within(tmp_path / "sub" / "b.txt", tmp_path)
    assert not is_within(tmp_path.parent / "outside.txt", tmp_path)


def test_confine_ok(tmp_path):
    sb = Sandbox(str(tmp_path))
    confined = sb.confine("sub/file.py")
    assert is_within(confined, tmp_path)


def test_confine_escape_raises(tmp_path):
    sb = Sandbox(str(tmp_path))
    with pytest.raises(SandboxViolation):
        sb.confine("../escape.txt")
    with pytest.raises(SandboxViolation):
        sb.confine(str(tmp_path.parent / "x.txt"))


def test_build_env_strips_unlisted_secret(tmp_path):
    sb = Sandbox(str(tmp_path))
    env = sb.build_env({"PATH": os.environ.get("PATH", ""), "MY_SECRET_KEY": "sk-deadbeef"})
    assert "MY_SECRET_KEY" not in env       # SECGOV-R3: secrets scrubbed
    assert "PATH" in env                     # required key kept


def test_deny_network_sets_blackhole(tmp_path):
    sb = Sandbox(str(tmp_path), deny_network=True)
    env = sb.build_env({"PATH": "x"})
    assert env.get("HTTP_PROXY") == "http://127.0.0.1:9"
    sb2 = Sandbox(str(tmp_path), deny_network=False)
    assert "HTTP_PROXY" not in sb2.build_env({"PATH": "x"})


def test_run_executes_in_root(tmp_path):
    sb = Sandbox(str(tmp_path))
    res = sb.run([sys.executable, "-c", "import os; print(os.getcwd())"])
    assert isinstance(res, SandboxResult)
    assert res.returncode == 0
    assert Path(res.stdout.strip()).resolve() == tmp_path.resolve()


def test_run_writes_only_into_root(tmp_path):
    sb = Sandbox(str(tmp_path))
    res = sb.run([sys.executable, "-c", "open('out.txt','w').write('hi')"])
    assert res.returncode == 0
    assert (tmp_path / "out.txt").read_text() == "hi"


def test_run_rejects_shell_string(tmp_path):
    sb = Sandbox(str(tmp_path))
    with pytest.raises(SandboxViolation):
        sb.run("echo hi")   # SAFE-R2 spirit: no shell strings


def test_run_enforced_on_win_or_posix(tmp_path):
    """SAFE-R1: an OS primitive (Job Object / process-group) backs the run."""
    sb = Sandbox(str(tmp_path))
    res = sb.run([sys.executable, "-c", "print('ok')"])
    assert res.returncode == 0
    if sys.platform == "win32" or os.name == "posix":
        assert res.enforced is True


def test_timeout_kills_process_tree(tmp_path):
    """Timeout terminates the process before it can finish its work — proving
    the OS-level kill actually fires (not just a detached child running on)."""
    sb = Sandbox(str(tmp_path))
    res = sb.run(
        [sys.executable, "-c",
         "import time; time.sleep(2); open('late.txt','w').write('x')"],
        timeout=0.5,
    )
    assert res.timed_out is True
    time.sleep(2.2)  # well past the child's 2s sleep
    assert not (tmp_path / "late.txt").exists()  # killed -> never wrote


def test_job_limits_does_not_break_run(tmp_path):
    sb = Sandbox(str(tmp_path),
                 job_limits=JobLimits(max_active_processes=16,
                                      max_memory_bytes=512 * 1024 * 1024))
    res = sb.run([sys.executable, "-c", "print('ok')"])
    assert res.returncode == 0
    assert res.stdout.strip() == "ok"
