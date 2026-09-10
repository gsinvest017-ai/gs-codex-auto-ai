"""Complete bounded matrix; runtime provider calls are prohibited."""
import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location("testcase_matrix", Path(__file__).with_name("matrix.py"))
matrix = importlib.util.module_from_spec(spec)
spec.loader.exec_module(matrix)


def test_complete_cartesian_matrix(tmp_path):
    report = matrix.run_matrix(tmp_path / "reports")
    assert report["coverage"]["parameter_combinations"] == matrix.expected_combination_count()
    assert report["coverage"]["execution_combinations"] == matrix.expected_combination_count() * len(matrix.OUTCOMES)
    failures = [(case["id"], check) for case in report["cases"] for check in case["checks"] + [item for execution in case["executions"] for item in execution["checks"]] if check["status"] == "failed"]
    assert failures == []
    assert report["generated_at"]
    assert report["source_sha256"]["testcase/matrix.py"]
    assert all(case["case_id"] and case["prompt_file"] and case["expected_artifacts"] for case in report["cases"])
    assert report["coverage"]["paid_calls"] == 0
    assert report["coverage"]["runtime_continuation"] == "NOT_VERIFIED"
    assert len({tuple(case["parameters"].values()) for case in report["cases"]}) == matrix.expected_combination_count()


def test_oracle_quota_and_fatal_truth_table():
    parameters = dict(nonstop=False, preset="codex-first", scenario="coding", primary="claude", model_class="blank", fallback_class="Gemini")
    assert matrix.oracle(parameters, "bothquota")["providers"] == ["claude", "codex", "opencode"]
    assert matrix.oracle(parameters, "genericerror")["providers"] == ["claude"]
    assert matrix.oracle({**parameters, "model_class": "mismatch"}, "bothquota")["providers"] == ["claude"]
    assert matrix.oracle({**parameters, "fallback_class": "blank"}, "bothquota")["ok"] is False
    opencode = {**parameters, "preset": "opencode-first", "primary": "opencode", "model_class": "explicit_valid"}
    assert matrix.oracle(opencode, "primaryok") == {"providers": ["opencode"], "ok": True, "configuration_rejected": False}
    for outcome in ("onequota", "bothquota", "genericerror"):
        assert matrix.oracle(opencode, outcome) == {"providers": ["opencode"], "ok": False, "configuration_rejected": False}
    for invalid in ({**opencode, "preset": "codex-first"}, {**opencode, "model_class": "blank"}):
        assert matrix.oracle(invalid, "primaryok")["configuration_rejected"]
    # Independently assert the newly required axes are actually present.
    assert set(matrix.DIMENSIONS["preset"]) == {"codex-first", "multi-provider", "claude-first", "opencode-first", "review-codex-build-claude"}
    assert set(matrix.DIMENSIONS["primary"]) == {"codex", "claude", "opencode"}

def test_fallback_model_boundaries_and_explicit_clear(tmp_path):
    from unittest.mock import patch
    import pytest
    router = matrix.router
    router.apply_preset(tmp_path, 'codex-first')
    config_path = tmp_path / 'log/model-routing.json'
    previous = config_path.read_bytes()
    for missing in (None, '', '   ', 'provider/', '/model'):
        with pytest.raises(ValueError):
            router.apply_preset(tmp_path, 'opencode-first', missing)
        assert config_path.read_bytes() == previous
    router.save_route(tmp_path, None, None, None, 'vendor/model')
    router.save_route(tmp_path, 'coding', 'codex', None)
    assert router.catalog(tmp_path)['fallback']['model'] == 'vendor/model'
    config = tmp_path / 'log/model-routing.json'
    before = config.read_bytes()
    for invalid in ('/', 'provider/', '/model', 'provider//model', 'provider/ model', '   '):
        with pytest.raises(ValueError):
            router.save_route(tmp_path, None, None, None, invalid)
        assert config.read_bytes() == before
        with patch.object(matrix.runner.shutil, 'which', return_value='controlled'), patch.object(matrix.runner, 'resolve_codex', return_value=['controlled']):
            with pytest.raises(ValueError):
                matrix.runner.provider_command({'provider': 'opencode', 'model': invalid}, 'fixture')
    router.save_route(tmp_path, None, None, None, '')
    assert router.catalog(tmp_path)['fallback']['model'] is None


def test_all_prompt_cases_inferred_under_all_presets(tmp_path):
    for preset in matrix.DIMENSIONS['preset']:
        matrix.apply_test_preset(tmp_path, preset)
        for case in matrix.prompt_cases():
            prompt = (Path(__file__).parent / case['prompt_file']).read_text(encoding='utf-8-sig')
            route = matrix.router.resolve_route(prompt, tmp_path)
            assert route['scenario'] == case['expected_scenario'], case['id']
            assert matrix.router.resolve_route(case['routing_text'], tmp_path)['scenario'] == case['expected_scenario'], case['id']
            expected = matrix.expected_preset_primary(preset, case['expected_scenario'])
            assert route['provider'] == expected, case['id']
