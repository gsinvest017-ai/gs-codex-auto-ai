"""Quota transitions are verified without spending real subscriptions."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
import codex_runner as runner
import model_router as router


@pytest.mark.parametrize("output", [
    '{"type":"error","error":{"code":"usage_limit_reached"}}',
    '{"is_error":true,"result":"You have hit your usage limit"}',
    "ERROR: usage quota exhausted",
])
def test_explicit_quota_errors(output):
    assert runner.quota_exhausted(output)


@pytest.mark.parametrize("output", [
    "ERROR: 429 rate limit exceeded", "ERROR: authentication failed", "timeout",
    '{"type":"text","part":{"text":"ERROR: usage quota exhausted"}}',
    '{"result":"quota_exhausted"}', 'I wrote ERROR: usage quota exhausted in docs',
    "ERROR: unknown quota", "ERROR: insufficient credits", "ERROR: quota probe failed",
])
def test_unknown_transient_and_model_output_do_not_unlock(output):
    assert not runner.quota_exhausted(output)


def policy(tmp_path):
    router.save_route(tmp_path, None, None, None, "google/gemini-test")


def execute(monkeypatch, tmp_path, capsys, failures):
    seen = []
    policy(tmp_path)
    monkeypatch.setattr(runner, "provider_command", lambda route, prompt: [route["provider"], prompt])
    def fake_run(cmd, *args, **kwargs):
        seen.append(cmd[0])
        kwargs["result_metadata"].update({"usage": {"input_tokens": 12, "output_tokens": 3, "cached_input_tokens": 4}, "actual_model": "reported-model", "usage_source": "cli_output"})
        reason = failures.get(cmd[0])
        return not reason, reason or "ok"
    monkeypatch.setattr(runner, "run_once", fake_run)
    code = runner.main(["--prompt", "code", "--cwd", str(tmp_path), "--retries", "1"])
    result = json.loads(capsys.readouterr().out)
    events = [json.loads(line) for line in (tmp_path / "log/events.jsonl").read_text().splitlines()]
    return code, seen, result, events


def test_both_quotas_unlock_real_third_attempt(monkeypatch, tmp_path, capsys):
    code, seen, result, events = execute(monkeypatch, tmp_path, capsys, {"codex": "quota_exhausted: reached", "claude": "quota_exhausted: reached"})
    assert code == 0 and seen == ["codex", "claude", "opencode"]
    assert result["actual_route"]["provider"] == "opencode"
    terminal = [e for e in events if e["outcome"] != "started"]
    assert [e["outcome"] for e in terminal] == ["quota_exhausted", "quota_exhausted", "ok"]
    assert terminal[-1]["requested_provider"] == "codex"
    assert terminal[-1]["actual_provider"] == "opencode"
    assert terminal[-1]["actual_model"] == "reported-model"
    assert terminal[-1]["usage"]["input_tokens"] == 12
    assert len({e["attempt_id"] for e in terminal}) == 3


@pytest.mark.parametrize("failure", ["timeout", "fatal:not_logged_in", "quota: rate limit", "unknown"])
def test_one_exhausted_other_not_exhausted_blocks_opencode(monkeypatch, tmp_path, capsys, failure):
    code, seen, _, _ = execute(monkeypatch, tmp_path, capsys, {"codex": "quota_exhausted: reached", "claude": failure})
    assert code == 1 and seen == ["codex", "claude"]


def test_available_primary_success_never_calls_secondary(monkeypatch, tmp_path, capsys):
    code, seen, _, _ = execute(monkeypatch, tmp_path, capsys, {})
    assert code == 0 and seen == ["codex"]


def test_primary_alternate_success(monkeypatch, tmp_path, capsys):
    code, seen, _, _ = execute(monkeypatch, tmp_path, capsys, {"codex": "quota_exhausted: reached"})
    assert code == 0 and seen == ["codex", "claude"]


def test_opencode_explicit_model_argv(monkeypatch):
    monkeypatch.setattr(runner.shutil, "which", lambda name: "/bin/" + name)
    assert runner.provider_command({"provider": "opencode", "model": "google/gemini-test"}, "a\nb") == ["/bin/opencode", "run", "--format", "json", "--model", "google/gemini-test", "a\nb"]
    with pytest.raises(ValueError, match="explicit"):
        runner.provider_command({"provider": "opencode", "model": None}, "a")


def test_legacy_secondary_policy_cannot_bypass_gate(tmp_path):
    for provider in ("gemini", "deepseek", "opencode"):
        route = router.resolve_route("code", tmp_path, provider=provider)
        assert route["provider"] == "codex"
        assert route["requested_provider"] == provider


def test_save_and_catalog(tmp_path):
    data = router.save_route(tmp_path, "review", "claude", "sonnet", "google/gemini-test")
    review = next(r for r in data["scenarios"] if r["name"] == "review")
    assert review["provider"] == "claude" and review["model"] == "sonnet"
    assert data["fallback"]["model"] == "google/gemini-test"


def test_usage_unknown_not_zero_or_configured_model():
    data = runner.output_metadata('{"type":"text","part":{"text":"done"}}')
    assert data["actual_model"] is None
    assert all(v is None for v in data["usage"].values())


def test_multiturn_usage_and_duplicate_ids():
    output = '\n'.join(json.dumps(p) for p in [
        {"type": "turn.completed", "turn_id": "one", "usage": {"input_tokens": 10, "output_tokens": 3}},
        {"type": "turn.completed", "turn_id": "two", "usage": {"input_tokens": 20, "output_tokens": 7}},
        {"type": "turn.completed", "turn_id": "two", "usage": {"input_tokens": 20, "output_tokens": 7}},
    ])
    assert runner.output_metadata(output)["usage"] == {"input_tokens": 30, "output_tokens": 10, "cached_input_tokens": None}


def test_open_code_multistep_usage():
    output = '\n'.join(json.dumps({"type": "step_finish", "part": {"id": str(i), "tokens": {"input": 10, "output": 5, "cache": {"read": 3}}}}) for i in range(2))
    assert runner.output_metadata(output)["usage"] == {"input_tokens": 20, "output_tokens": 10, "cached_input_tokens": 6}


def test_claude_aggregate_not_double_counted():
    output = '\n'.join(json.dumps(p) for p in [
        {"type": "assistant", "usage": {"input_tokens": 10, "output_tokens": 2}},
        {"type": "result", "usage": {"input_tokens": 25, "output_tokens": 8, "cache_read_input_tokens": 4}},
    ])
    assert runner.output_metadata(output)["usage"] == {"input_tokens": 25, "output_tokens": 8, "cached_input_tokens": 4}


def test_dispatcher_and_writer_tool_roles(monkeypatch):
    monkeypatch.setattr(runner.shutil, "which", lambda name: "/bin/" + name)
    dispatcher = runner.provider_command({"provider": "claude", "model": None, "role": "dispatcher"}, "task")
    assert "--tools" not in dispatcher
    writer = runner.provider_command({"provider": "claude", "model": None, "role": "writer"}, "task")
    assert "Write" in writer[writer.index("--tools") + 1]
    assert "acceptEdits" in writer


def test_dispatcher_env_allows_children_and_records_parent(tmp_path):
    program = "import os,json; print(json.dumps({'result':'done','worker':os.environ.get('CODEXAUTOAI_ROUTED_WORKER'), 'parent':os.environ.get('CODEXAUTOAI_PARENT_RUN_ID')}))"
    metadata = {}
    ok, _ = runner.run_once([sys.executable, "-c", program], tmp_path, [], 1, 1, poll=0.01,
                            provider="claude", role="dispatcher", attempt_id="root-id:1", result_metadata=metadata)
    assert ok
    result = json.loads(Path(metadata["result_path"]).read_text())
    assert result["worker"] is None and result["parent"] == "root-id"


def test_real_fake_process_fallback_writes_artifact(monkeypatch, tmp_path, capsys):
    policy(tmp_path)
    def command(route, prompt):
        if route["provider"] in ("codex", "claude"):
            script = "import json,sys; print(json.dumps({'type':'error','error':{'code':'usage_limit_reached'}})); sys.exit(1)"
        else:
            script = "import json,pathlib; pathlib.Path('artifact.txt').write_text('verified'); print(json.dumps({'type':'text','part':{'text':'done'}})); print(json.dumps({'type':'step_finish','part':{'id':'1','tokens':{'input':11,'output':7}}}))"
        return [sys.executable, "-c", script]
    monkeypatch.setattr(runner, "provider_command", command)
    assert runner.main(["--prompt", "code", "--expect", "artifact.txt", "--cwd", str(tmp_path), "--retries", "1"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["actual_route"]["provider"] == "opencode"
    assert (tmp_path / "artifact.txt").read_text() == "verified"
    assert result["usage"]["input_tokens"] == 11
    events = [json.loads(line) for line in (tmp_path / "log/events.jsonl").read_text().splitlines()]
    assert [e["actual_provider"] for e in events if e["outcome"] == "quota_exhausted"] == ["codex", "claude"]
    assert events[-1]["outcome"] == "ok" and events[-1]["role"] == "writer"


def test_fake_process_claude_quota_fallback_writer(monkeypatch, tmp_path, capsys):
    def command(route, prompt):
        if route["provider"] == "codex":
            script = "import json,sys; print(json.dumps({'type':'error','error':{'code':'usage_limit_reached'}})); sys.exit(1)"
        else:
            script = "import json,pathlib; pathlib.Path('artifact.txt').write_text('verified'); print(json.dumps({'type':'result','result':'done','usage':{'input_tokens':11,'output_tokens':7}}))"
        return [sys.executable, "-c", script]
    monkeypatch.setattr(runner, "provider_command", command)
    assert runner.main(["--prompt", "code", "--expect", "artifact.txt", "--cwd", str(tmp_path), "--retries", "1"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["actual_route"]["provider"] == "claude"
    events = [json.loads(line) for line in (tmp_path / "log/events.jsonl").read_text().splitlines()]
    started = next(e for e in events if e["actual_provider"] == "claude" and e["outcome"] == "started")
    assert started["role"] == "writer" and started["authorization_expires_at"] > 0


def test_fake_dispatcher_uses_same_gate(monkeypatch, tmp_path, capsys):
    policy(tmp_path)
    def command(route, prompt):
        assert route.get("role") in (None, "dispatcher")
        script = ("import json,sys; print(json.dumps({'type':'error','error':{'code':'usage_limit_reached'}})); sys.exit(1)"
                  if route["provider"] in ("codex", "claude") else
                  "import json; print(json.dumps({'type':'text','part':{'text':'done'}}))")
        return [sys.executable, "-c", script]
    monkeypatch.setattr(runner, "provider_command", command)
    assert runner.main(["--dispatcher", "--prompt", "code", "--cwd", str(tmp_path), "--retries", "1"]) == 0
    result = json.loads(capsys.readouterr().out)
    events = [json.loads(line) for line in (tmp_path / "log/events.jsonl").read_text().splitlines()]
    assert all(e["role"] == "dispatcher" and e["parent_run_id"] == result["run_id"] for e in events)
