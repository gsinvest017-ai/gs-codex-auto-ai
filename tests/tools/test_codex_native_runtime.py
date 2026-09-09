"""Native dispatch configuration is local to Codex dispatcher invocations."""
import json
import sys
import tomllib
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
import codex_runner as runner


def settings(command):
    return tomllib.loads("\n".join(command[i + 1] for i, value in enumerate(command) if value == "-c"))


@pytest.mark.parametrize("model", [None, 'model-with-"quote"\\suffix'])
def test_native_dispatch_argv_is_valid_toml_and_preserves_prompt(monkeypatch, model):
    monkeypatch.setattr(runner, "resolve_codex", lambda _: ["codex"])
    monkeypatch.setattr(runner.shutil, "which", lambda _: "codex")
    command = runner.provider_command({"provider": "codex", "model": model, "role": "dispatcher"}, "task\n$literal")
    config = settings(command)
    assert command[-1] == "task\n$literal"
    assert command[command.index("--sandbox") + 1] == "workspace-write"
    assert config["features"]["multi_agent"] is True
    assert config["agents"]["enabled"] is True
    assert config["developer_instructions"] == runner.CODEX_DISPATCHER_RUNTIME
    if model:
        assert config["agents"]["default_subagent_model"] == model
    else:
        assert "default_subagent_model" not in config["agents"]
    assert not any("dangerously" in argument for argument in command)


def test_native_override_does_not_leak_into_codex_worker_or_claude(monkeypatch):
    monkeypatch.setattr(runner, "resolve_codex", lambda name: [name])
    monkeypatch.setattr(runner.shutil, "which", lambda name: name)
    for provider, role in [("codex", "worker"), ("codex", "writer"), ("claude", "dispatcher")]:
        command = runner.provider_command({"provider": provider, "model": None, "role": role}, "task")
        assert "developer_instructions" not in json.dumps(command)
        assert "features.multi_agent" not in json.dumps(command)
    claude = runner.provider_command({"provider": "claude", "model": None, "role": "dispatcher"}, "task")
    assert "codex_runner.py" in claude[claude.index("--append-system-prompt") + 1]


def test_native_events_do_not_double_parent_usage_or_claim_unknown_success(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(runner, "provider_command", lambda route, prompt: ["fake"])
    parent_usage = {"input_tokens": 20, "output_tokens": 3, "cached_input_tokens": 10}
    def fake_run(*args, **kwargs):
        kwargs["result_metadata"].update(result_path=str(tmp_path / "result.jsonl"), usage=parent_usage)
        return True, "ok"
    monkeypatch.setattr(runner, "run_once", fake_run)
    child_usage = {"input_tokens": 8, "output_tokens": 2, "cached_input_tokens": 4}
    monkeypatch.setattr(runner, "collect_native_children", lambda *args: [
        {"thread_id": "child", "parent_thread_id": "parent", "status": "ok", "usage": child_usage,
         "usage_source": "codex_rollout", "actual_model": "observed-model", "source_path": "rollout.jsonl"},
        {"thread_id": "unfinished", "parent_thread_id": "parent", "status": "unknown",
         "usage": {key: None for key in child_usage}, "usage_source": "codex_rollout", "source_path": "other.jsonl"},
    ])
    assert runner.main(["--dispatcher", "--prompt", "task", "--cwd", str(tmp_path), "--retries", "1"]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["usage"] == parent_usage
    assert result["attempts"] == 1 and result["native_children_observed"] == 2
    events = [json.loads(line) for line in (tmp_path / "log/events.jsonl").read_text(encoding="utf-8").splitlines()]
    child = next(event for event in events if event["attempt_id"] == "native:child")
    assert child["usage"] == child_usage and child["actual_model"] == "observed-model"
    assert child["run_id"] == "child" and child["parent_thread_id"] == "parent"
    assert child["usage_scope"] == "native_agent_rollout"
    assert child["parent_run_id"] == result["run_id"] and child["outcome"] == "ok"
    unfinished = next(event for event in events if event["attempt_id"] == "native:unfinished")
    assert unfinished["outcome"] == "started" and unfinished["actual_model"] is None


def test_nested_codex_dispatcher_rejected_before_any_provider_launch(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("CODEXAUTOAI_ROUTED_PROVIDER", "codex")
    monkeypatch.setenv("CODEXAUTOAI_ROUTED_ROLE", "dispatcher")
    monkeypatch.delenv("CODEXAUTOAI_ROUTED_WORKER", raising=False)
    monkeypatch.setattr(runner, "provider_command", lambda *args: pytest.fail("must not launch a CLI"))
    assert runner.main(["--prompt", "hello", "--cwd", str(tmp_path)]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["fatal"] and result["attempts"] == 0
    assert "native Codex subagents" in result["reason"]
    assert not runner.quota_exhausted(result["reason"])


@pytest.mark.parametrize("parent_provider", [None, "claude"])
def test_host_and_claude_dispatcher_can_still_launch_worker(monkeypatch, tmp_path, capsys, parent_provider):
    monkeypatch.delenv("CODEXAUTOAI_ROUTED_WORKER", raising=False)
    if parent_provider:
        monkeypatch.setenv("CODEXAUTOAI_ROUTED_PROVIDER", parent_provider)
        monkeypatch.setenv("CODEXAUTOAI_ROUTED_ROLE", "dispatcher")
    else:
        monkeypatch.delenv("CODEXAUTOAI_ROUTED_PROVIDER", raising=False)
        monkeypatch.delenv("CODEXAUTOAI_ROUTED_ROLE", raising=False)
    monkeypatch.setattr(runner, "provider_command", lambda *args: ["fake"])
    monkeypatch.setattr(runner, "run_once", lambda *args, **kwargs: (True, "ok"))
    assert runner.main(["--prompt", "hello", "--cwd", str(tmp_path)]) == 0
    assert json.loads(capsys.readouterr().out)["attempts"] == 1


@pytest.mark.parametrize("provider", ["codex", "claude"])
def test_child_env_provider_overwrites_inherited_marker(monkeypatch, tmp_path, provider):
    monkeypatch.setenv("CODEXAUTOAI_ROUTED_PROVIDER", "stale-parent")
    monkeypatch.setenv("CODEXAUTOAI_ROUTED_ROLE", "stale-role")
    program = "import os,json; print(json.dumps({'result':'done','provider':os.environ.get('CODEXAUTOAI_ROUTED_PROVIDER'),'role':os.environ.get('CODEXAUTOAI_ROUTED_ROLE')}))"
    metadata = {}
    ok, _ = runner.run_once([sys.executable, "-c", program], tmp_path, [], 5, 5, poll=0.01,
                            provider=provider, role="dispatcher", result_metadata=metadata)
    assert ok
    output = json.loads(Path(metadata["result_path"]).read_text(encoding="utf-8"))
    assert output["provider"] == provider and output["role"] == "dispatcher"
