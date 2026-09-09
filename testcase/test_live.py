"""Harness contract tests; no provider execution."""
import json
from pathlib import Path
import pytest
from testcase import run_live


def test_report_never_passes_when_required_artifact_is_missing():
    assert run_live.final_status('completed', {'model.glb': False}) == 'incomplete'
    assert run_live.final_status('completed', {'model.glb': True}) == 'completed'
    assert run_live.final_status('failed', {'model.glb': True}) == 'failed'


def test_default_plan_never_starts_provider(monkeypatch, capsys):
    monkeypatch.setattr(run_live.subprocess, 'Popen', lambda *a, **k: pytest.fail('paid process started'))
    assert run_live.main([]) == 0
    plan=json.loads(capsys.readouterr().out)
    assert plan['mode']=='dry_run' and plan['requested_nonstop'] is False
    assert plan['native_agent_limit']==1 and plan['ui_verified'] is False


def test_execute_bootstrap_contains_real_engine_and_planned_route_before_spawn(tmp_path, monkeypatch):
    (tmp_path/'Desktop').mkdir()
    monkeypatch.setattr(Path,'home',lambda:tmp_path)
    class StopBeforeProvider(Exception): pass
    def inspect_spawn(command, **kwargs):
        root=Path(kwargs['cwd'])
        assert (root/'src/codexautoai_v2').is_dir()
        assert (root/'tools/run_phase.py').is_file()
        for relative in ('docs/templates','.claude','.githooks'):
            if (run_live.REPO/relative).is_dir(): assert (root/relative).is_dir()
        app=json.loads((root/'log/app-run.json').read_text(encoding='utf-8'))
        assert app['route']['scenario']=='3d_modeling' and app['route']['provider']=='codex'
        assert app['requested_nonstop'] is False
        assert '--dispatcher' in command
        raise StopBeforeProvider
    monkeypatch.setattr(run_live.subprocess,'Popen',inspect_spawn)
    with pytest.raises(StopBeforeProvider): run_live.main(['--execute'])
