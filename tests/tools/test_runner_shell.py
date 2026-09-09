"""Windows shell selection must not rely on Store aliases or alter global state."""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
import codex_runner as runner


def test_windows_alias_removed_without_mutation(monkeypatch):
    monkeypatch.setattr(runner, "IS_WIN", True)
    original = {"Path": r"C:\Python;C:\Users\U\AppData\Local\Microsoft\WindowsApps;C:\Git\bin;C:\real\pwsh", "OTHER": "unchanged"}
    snapshot = original.copy()
    process_snapshot = dict(os.environ)
    child = runner.codex_shell_environment(original)
    assert "WindowsApps" not in child["Path"]
    for path in (r"C:\Python", r"C:\Git\bin", r"C:\real\pwsh"):
        assert path in child["Path"].split(";")
    assert child["OTHER"] == "unchanged"
    assert original == snapshot
    assert dict(os.environ) == process_snapshot


def test_alias_case_quotes_and_trailing_slash(monkeypatch):
    monkeypatch.setattr(runner, "IS_WIN", True)
    child = runner.codex_shell_environment({"PATH": '"C:/Users/U/AppData/Local/Microsoft/WindowsApps/";C:/valid'})
    assert "WindowsApps" not in child["PATH"]
    assert "C:/valid" in child["PATH"].split(";")


def test_non_windows_environment_unchanged(monkeypatch):
    monkeypatch.setattr(runner, "IS_WIN", False)
    original = {"PATH": "/usr/bin:/bin", "SHELL": "/bin/bash"}
    assert runner.codex_shell_environment(original) == original
    assert runner.codex_shell_environment(original) is not original


def test_windows_provider_uses_non_login_without_relaxing_sandbox(monkeypatch):
    monkeypatch.setattr(runner, "IS_WIN", True)
    monkeypatch.setattr(runner.shutil, "which", lambda _: "codex.exe")
    command = runner.provider_command({"provider": "codex", "model": None}, "bounded test")
    assert "allow_login_shell=false" in command
    assert command[command.index("--sandbox") + 1] == "workspace-write"
    assert "--dangerously-bypass-approvals-and-sandbox" not in command
