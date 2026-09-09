"""Delivery status must not be inferred from a successful model process."""
import json
import sys
import time
import os
import subprocess
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
import codex_runner as runner
import run_phase


def finish(root, monkeypatch, run_id="current"):
    monkeypatch.setenv("CODEXAUTOAI_PARENT_RUN_ID", run_id)
    (root / "delivery.md").write_text("Verified delivery and execution instructions", encoding="utf-8")
    run_phase.cmd_start(root, None)
    run_phase.cmd_begin(root, "7", None)
    run_phase.cmd_end(root, "7", "success", None, None, ["delivery.md"])


def outcome(root, started=0, offset=0):
    return runner.dispatcher_outcome(root, "current", started, offset, True, "ok")


def test_cli_success_without_pipeline_is_incomplete(tmp_path):
    assert outcome(tmp_path)["status"] == "incomplete"


def test_fresh_phase7_delivery_completes(tmp_path, monkeypatch):
    started = time.time()
    finish(tmp_path, monkeypatch)
    assert outcome(tmp_path, started)["status"] == "completed"
    (tmp_path / "delivery.md").write_text("tampered", encoding="utf-8")
    assert outcome(tmp_path, started)["status"] == "incomplete"


@pytest.mark.parametrize("stale_kind", ["other_run", "timestamp", "offset"])
def test_prior_delivery_cannot_complete_new_task(tmp_path, monkeypatch, stale_kind):
    finish(tmp_path, monkeypatch, "old" if stale_kind == "other_run" else "current")
    offset = (tmp_path / "log/events.jsonl").stat().st_size if stale_kind == "offset" else 0
    assert outcome(tmp_path, time.time() if stale_kind == "timestamp" else 0, offset)["status"] == "incomplete"


def test_phase_failure_is_blocked(tmp_path, monkeypatch):
    finish(tmp_path, monkeypatch)
    run_phase.cmd_end(tmp_path, "1", "failure", "shell_denied", None)
    assert outcome(tmp_path)["status"] == "blocked"


def test_phase7_requires_real_artifacts(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEXAUTOAI_PARENT_RUN_ID", "current")
    with pytest.raises(ValueError):
        run_phase.cmd_end(tmp_path, "7", "success", None, None)
    assert outcome(tmp_path)["status"] == "incomplete"


def test_parent_run_overrides_stale_pointer_and_rejects_explicit_conflict(tmp_path, monkeypatch):
    run_phase.cmd_start(tmp_path, "stale")
    monkeypatch.setenv("CODEXAUTOAI_PARENT_RUN_ID", "current")
    run_phase.cmd_begin(tmp_path, "1", None)
    assert (tmp_path / "log/current_run.txt").read_text().strip() == "current"
    with pytest.raises(ValueError):
        run_phase.cmd_begin(tmp_path, "7", "stale")


def test_structured_environment_failure_is_blocked(tmp_path):
    output = tmp_path / "output.log"
    output.write_text(json.dumps({"type": "item.completed", "item": {"type": "command_execution",
        "aggregated_output": "CreateProcessAsUserW failed: 5"}}), encoding="utf-8")
    assert runner.dispatcher_outcome(tmp_path, "current", 0, 0, True, "ok", str(output))["status"] == "blocked"


@pytest.mark.parametrize("delivered", [False, True])
def test_main_writes_task_contract_and_preserves_model_metrics(tmp_path, monkeypatch, capsys, delivered):
    monkeypatch.setenv("CODEXAUTOAI_PARENT_RUN_ID", "current")
    monkeypatch.setattr(runner, "provider_command", lambda *args: ["fake"])
    def call(*args, **kwargs):
        if delivered:
            finish(tmp_path, monkeypatch)
        return True, "ok"
    monkeypatch.setattr(runner, "run_once", call)
    code = runner.main(["--dispatcher", "--prompt", "test", "--cwd", str(tmp_path)])
    result = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert code == (0 if delivered else 1)
    assert result["status"] == ("completed" if delivered else "incomplete")
    contract = json.loads((tmp_path / "log/task-result-current.json").read_text(encoding="utf-8"))
    assert contract["status"] == result["status"]
    events = [json.loads(line) for line in (tmp_path / "log/events.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [e for e in events if e.get("type") == "model_attempt"][-1]["outcome"] == "ok"


@pytest.mark.parametrize("delivered", [False, True])
def test_real_subprocess_dispatcher_contract(tmp_path, delivered):
    tools = Path(__file__).resolve().parents[2] / "tools"
    child = tmp_path / "fake_cli.py"
    child.write_text(
        "import sys, os, json\nfrom pathlib import Path\n"
        + (f"sys.path.insert(0, {str(tools)!r})\nimport run_phase\n"
           "root=Path.cwd()\n(root/'delivery.md').write_text('Verified output')\n"
           "run_phase.cmd_start(root,None)\nrun_phase.cmd_begin(root,'7',None)\n"
           "run_phase.cmd_end(root,'7','success',None,None,['delivery.md'])\n" if delivered else "")
        + "print(json.dumps({'type':'turn.completed','usage':{'input_tokens':1,'output_tokens':1}}))\n",
        encoding="utf-8")
    env = os.environ.copy()
    env.pop("CODEXAUTOAI_ROUTED_WORKER", None)
    env["CODEXAUTOAI_PARENT_RUN_ID"] = "subprocess-parent"
    env["PYTHONUTF8"] = "1"
    completed = subprocess.run([sys.executable, str(tools / "codex_runner.py"), "--dispatcher",
        "--prompt", "test", "--cwd", str(tmp_path), "--codex-cmd",
        f'"{Path(sys.executable).as_posix()}" "{child.as_posix()}"',
        "--retries", "1", "--timeout", "10"], env=env, capture_output=True, text=True, encoding="utf-8", timeout=20)
    assert completed.returncode == (0 if delivered else 1), completed.stderr
    contract = json.loads((tmp_path / "log/task-result-subprocess-parent.json").read_text(encoding="utf-8"))
    assert contract["status"] == ("completed" if delivered else "incomplete")
