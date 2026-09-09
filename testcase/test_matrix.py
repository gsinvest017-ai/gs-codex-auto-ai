"""Complete bounded matrix; runtime provider calls are prohibited."""
import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location("testcase_matrix", Path(__file__).with_name("matrix.py"))
matrix = importlib.util.module_from_spec(spec)
spec.loader.exec_module(matrix)


def test_complete_cartesian_matrix(tmp_path):
    report = matrix.run_matrix(tmp_path / "reports")
    assert report["coverage"]["parameter_combinations"] == 1120
    assert report["coverage"]["execution_combinations"] == 4480
    failures = [(case["id"], check) for case in report["cases"] for check in case["checks"] + [item for execution in case["executions"] for item in execution["checks"]] if check["status"] == "failed"]
    assert failures == []
    assert report["generated_at"]
    assert report["source_sha256"]["testcase/matrix.py"]
    assert all(case["case_id"] and case["prompt_file"] and case["expected_artifacts"] for case in report["cases"])
    assert report["coverage"]["paid_calls"] == 0
    assert report["coverage"]["runtime_continuation"] == "NOT_VERIFIED"
    assert len({tuple(case["parameters"].values()) for case in report["cases"]}) == 1120


def test_oracle_quota_and_fatal_truth_table():
    parameters = dict(nonstop=False, preset="codex-first", scenario="coding", primary="claude", model_class="blank", fallback_class="Gemini")
    assert matrix.oracle(parameters, "bothquota")["providers"] == ["claude", "codex", "opencode"]
    assert matrix.oracle(parameters, "genericerror")["providers"] == ["claude"]
    assert matrix.oracle({**parameters, "model_class": "mismatch"}, "bothquota")["providers"] == ["claude"]
    assert matrix.oracle({**parameters, "fallback_class": "blank"}, "bothquota")["ok"] is False

def test_fallback_model_boundaries_and_explicit_clear(tmp_path):
    from unittest.mock import patch
    import pytest
    router = matrix.router
    router.apply_preset(tmp_path, 'codex-first')
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


def test_all_prompt_cases_inferred_under_both_presets(tmp_path):
    for preset in ('codex-first', 'multi-provider'):
        matrix.router.apply_preset(tmp_path, preset)
        for case in matrix.prompt_cases():
            prompt = (Path(__file__).parent / case['prompt_file']).read_text(encoding='utf-8-sig')
            route = matrix.router.resolve_route(prompt, tmp_path)
            assert route['scenario'] == case['expected_scenario'], case['id']
            assert matrix.router.resolve_route(case['routing_text'], tmp_path)['scenario'] == case['expected_scenario'], case['id']
            expected = 'claude' if preset == 'multi-provider' and case['expected_scenario'] in ('review', 'research') else 'codex'
            assert route['provider'] == expected, case['id']
