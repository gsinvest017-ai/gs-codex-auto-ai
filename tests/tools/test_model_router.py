"""Routing policy and actual runner adapter contracts; no paid model calls."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import model_router as router
import codex_runner as runner


@pytest.mark.parametrize("prompt", ["make a 3D model", "幫我做3D模型", "用Blender製作角色", "建立mesh物件", "建立角色建模"])
def test_spatial_tasks(prompt, tmp_path):
    route = router.resolve_route(prompt, tmp_path)
    assert (route["scenario"], route["provider"], route["model"]) == ("3d_modeling", "codex", "gpt-6-astra")


@pytest.mark.parametrize("prompt", ["不要做3D模型", "no Blender please", "machine learning modeling", "機器學習建模", "數學建模", "3D tensor machine learning", "debug meshless solver"])
def test_false_positives(prompt, tmp_path):
    assert router.resolve_route(prompt, tmp_path)["scenario"] != "3d_modeling"


def test_negated_clause_does_not_hide_positive(tmp_path):
    assert router.resolve_route("No Blender; create a 3D mesh", tmp_path)["scenario"] == "3d_modeling"


def write_config(tmp_path, config):
    (tmp_path / "log").mkdir(exist_ok=True)
    (tmp_path / "log/model-routing.json").write_text(json.dumps(config), encoding="utf-8")


def test_model_override_preserves_legacy_codex(tmp_path):
    router.apply_preset(tmp_path, "multi-provider")
    route = router.resolve_route("research topic", tmp_path, model="manual")
    assert (route["provider"], route["model"]) == ("codex", "manual")
    route = router.resolve_route("make 3D", tmp_path, provider="claude", model="chosen")
    assert (route["provider"], route["model"]) == ("claude", "chosen")


def test_override_inherits_keywords_and_priority(tmp_path):
    write_config(tmp_path, {"scenarios": {"3d_modeling": {"model": "other"}, "special": {"provider": "gemini", "model": None, "keywords": ["Blender"], "priority": 10}}})
    assert router.resolve_route("3D model", tmp_path)["model"] == "other"
    assert router.resolve_route("Blender", tmp_path)["scenario"] == "special"


def test_explicit_scenario_and_provider(tmp_path):
    route = router.resolve_route("3D", tmp_path, scenario="research", provider="gemini")
    assert route["scenario"] == "research" and route["model"] is None
    with pytest.raises(ValueError, match="unknown scenario"):
        router.resolve_route("x", tmp_path, scenario="typo")


def test_invalid_policy_fails(tmp_path):
    write_config(tmp_path, {"scenarios": {"research": {"provider": "unknown"}}})
    with pytest.raises(ValueError, match="provider"):
        router.resolve_route("research", tmp_path)


def test_preset_routes_and_backup(tmp_path):
    first = router.apply_preset(tmp_path, "multi-provider")
    assert first["backup_path"] is None
    for prompt, provider in [("research", "claude"), ("review", "claude"), ("document", "codex"), ("coding", "codex"), ("debugging", "codex")]:
        assert router.resolve_route(prompt, tmp_path)["provider"] == provider
    next_result = router.apply_preset(tmp_path, "codex-first")
    assert Path(next_result["backup_path"]).exists()
    assert router.resolve_route("research", tmp_path)["provider"] == "codex"


@pytest.mark.parametrize("provider", ["codex", "claude", "gemini"])
def test_actual_adapter_argv(monkeypatch, provider):
    monkeypatch.setattr(runner.shutil, "which", lambda value: "/bin/" + value)
    command = runner.provider_command({"provider": provider, "model": "chosen"}, "hello\nworld")
    assert command[0] == "/bin/" + provider
    if provider == "codex":
        assert command[1:] == ["exec", "--sandbox", "workspace-write", "--skip-git-repo-check", "--json", "-m", "chosen", "hello\nworld"]
    else:
        assert command[command.index("--model") + 1] == "chosen"
        assert command[command.index("--output-format") + 1] == "json"
        assert command[command.index("-p") + 1].endswith("hello\nworld")
        if provider == "claude":
            assert command[command.index("--tools") + 1] == "Read,Glob,Grep,WebSearch,WebFetch"
            assert "--append-system-prompt" in command
        else:
            assert command[command.index("--approval-mode") + 1] == "plan"


def test_missing_provider_is_fatal_without_retry(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(runner.shutil, "which", lambda value: None)
    assert runner.main(["--prompt", "3D", "--cwd", str(tmp_path)]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["attempts"] == 0 and result["fatal"] and "unavailable" in result["reason"]


def test_context_only_fallback_and_watchdog_provider(monkeypatch, tmp_path, capsys):
    seen = []
    monkeypatch.setenv("CODEXAUTOAI_TASK_PROMPT", "create a 3D model")
    monkeypatch.setattr(runner, "provider_command", lambda route, prompt: [route["provider"], prompt])
    monkeypatch.setattr(runner, "run_once", lambda *args, **kwargs: (seen.append(kwargs) or True, "ok"))
    assert runner.main(["--prompt", "do next step", "--cwd", str(tmp_path)]) == 0
    assert json.loads(capsys.readouterr().out)["route"]["scenario"] == "3d_modeling"
    assert seen[-1]["provider"] == "codex"
    assert runner.main(["--prompt", "review changes", "--cwd", str(tmp_path)]) == 0
    assert json.loads(capsys.readouterr().out)["route"]["scenario"] == "review"


def test_non_codex_does_not_poll_codex_sessions(monkeypatch, tmp_path):
    monkeypatch.setattr(runner, "_latest_session_mtime", lambda *_: pytest.fail("wrong provider heartbeat"))
    assert runner.run_once([sys.executable, "-c", 'print("{\\\"response\\\":\\\"done\\\"}")'], tmp_path, [], 0.01, 0.01, poll=0.01, provider="claude", timeout=5)[0]


def test_structured_error_exit_zero_rejected(tmp_path):
    result = runner.run_once([sys.executable, "-c", 'print(\'{"is_error": true, "result": "denied"}\')'], tmp_path, [], 1, 1, poll=0.01, provider="claude", timeout=5)
    assert not result[0] and "provider_error" in result[1]


def test_non_codex_timeout_and_expects(tmp_path):
    result = runner.run_once([sys.executable, "-c", "import time; time.sleep(10)"], tmp_path, [], 1, 1, poll=0.01, provider="gemini", timeout=0.1)
    assert not result[0] and "timeout" in result[1]
    result = runner.run_once([sys.executable, "-c", 'print("{\\\"response\\\":\\\"done\\\"}")'], tmp_path, ["missing.txt"], 1, 1, poll=0.01, provider="gemini", timeout=5)
    assert not result[0] and "expects_ok=False" in result[1]


@pytest.mark.parametrize("prompt,scenario", [
    ("review the Blender addon code", "review"),
    ("Research Blender pricing", "research"),
    ("不要3D，只要寫程式", "coding"),
    ("請建立三維角色，不要使用Blender", "3d_modeling"),
    ("build Blender model and document it", "3d_modeling"),
])
def test_task_intent_and_local_negation(prompt, scenario, tmp_path):
    assert router.resolve_route(prompt, tmp_path)["scenario"] == scenario


@pytest.mark.parametrize("writer_ok", [True, False])
def test_two_stage_writer_controls_success(writer_ok, monkeypatch, tmp_path, capsys):
    seen = []
    monkeypatch.setattr(runner, "provider_command", lambda route, prompt: [route["provider"], prompt])
    def fake_run(cmd, cwd, expects, *args, **kwargs):
        seen.append((cmd[0], list(expects)))
        if cmd[0] == "claude":
            metadata = kwargs["result_metadata"]
            metadata["result_path"] = str(tmp_path / "review.log")
            Path(metadata["result_path"]).write_text('{"result":"findings"}')
            return True, "ok"
        return writer_ok, "ok" if writer_ok else "failed"
    monkeypatch.setattr(runner, "run_once", fake_run)
    rc = runner.main(["--prompt", "review", "--provider", "claude", "--expect", "docs/review.md", "--cwd", str(tmp_path), "--retries", "1"])
    result = json.loads(capsys.readouterr().out)
    assert rc == (0 if writer_ok else 1)
    assert seen == [("claude", []), ("codex", ["docs/review.md"])]
    assert result["writer_route"]["provider"] == "codex"
    assert result["writer_route"]["model"] is None


def test_failed_analysis_never_starts_writer(monkeypatch, tmp_path, capsys):
    seen = []
    monkeypatch.setattr(runner, "provider_command", lambda route, prompt: [route["provider"], prompt])
    monkeypatch.setattr(runner, "run_once", lambda cmd, *args, **kwargs: (seen.append(cmd[0]) or False, "fatal:analysis_failed"))
    assert runner.main(["--prompt", "review", "--provider", "claude", "--expect", "docs/review.md", "--cwd", str(tmp_path)]) == 1
    capsys.readouterr()
    assert seen == ["claude"]


def test_generic_coding_inherits_spatial_parent(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("CODEXAUTOAI_TASK_PROMPT", "create a 3D model")
    monkeypatch.setattr(runner, "provider_command", lambda route, prompt: [route["provider"], prompt])
    monkeypatch.setattr(runner, "run_once", lambda *args, **kwargs: (True, "ok"))
    assert runner.main(["--prompt", "實作函式", "--cwd", str(tmp_path)]) == 0
    assert json.loads(capsys.readouterr().out)["route"]["model"] == "gpt-6-astra"


def test_result_artifact_survives_non_codex_success(tmp_path):
    metadata = {}
    ok, _ = runner.run_once([sys.executable, "-c", 'print(\'{"result":"review passed"}\')'], tmp_path, [], 1, 1, poll=0.01, provider="claude", timeout=5, result_metadata=metadata)
    assert ok
    result = Path(metadata["result_path"])
    assert result.parent == tmp_path / "log/model-routing-results"
    assert json.loads(result.read_text())["result"] == "review passed"


def test_worker_cannot_recurse(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("CODEXAUTOAI_ROUTED_WORKER", "claude")
    assert runner.main(["--prompt", "code", "--cwd", str(tmp_path)]) == 1
    assert "recursively" in json.loads(capsys.readouterr().out)["reason"]


@pytest.mark.parametrize("timeout", ["nan", "inf", "-inf", "0"])
def test_unbounded_timeout_rejected(timeout, tmp_path, capsys):
    assert runner.main(["--prompt", "x", "--cwd", str(tmp_path), "--timeout=" + timeout]) == 1
    assert "positive" in json.loads(capsys.readouterr().out)["reason"]


@pytest.mark.parametrize("payload", [{}, {"result": ""}, {"result": "ok", "permission_denials": [{"tool": "Write"}]}])
def test_empty_or_permission_denied_result_rejected(payload, tmp_path):
    path = tmp_path / "output.json"
    path.write_text(json.dumps(payload))
    assert runner.provider_reported_error(path)



def test_native_claude_shim_keeps_multiline_prompt(tmp_path, monkeypatch):
    executable = tmp_path / "node_modules/@anthropic-ai/claude-code/bin/claude.exe"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"fake native cli")
    shim = tmp_path / "claude.cmd"
    shim.write_text('@ECHO off\n"%dp0%\\node_modules\\@anthropic-ai\\claude-code\\bin\\claude.exe" %*\n')
    monkeypatch.setattr(runner.shutil, "which", lambda _: str(shim))
    prompt = "first line\nsecond line & literal"
    argv = runner.provider_command({"provider": "claude", "model": None}, prompt)
    assert Path(argv[0]) == executable
    assert argv[argv.index("-p") + 1] == prompt
    assert not any(str(arg).endswith(".cmd") for arg in argv)



def test_real_cli_emits_utf8_despite_legacy_stdio_encoding(tmp_path):
    result = subprocess.run(
        [sys.executable, str(ROOT / "tools/model_router.py"), "--root", str(tmp_path),
         "--prompt", "請審查程式", "--json"],
        capture_output=True, check=True, timeout=10,
        env={**os.environ, "PYTHONIOENCODING": "cp950"},
    )
    route = json.loads(result.stdout.decode("utf-8", errors="strict"))
    assert route["scenario"] == "review"
    assert route["reason"] == "matched keyword: 審查"
