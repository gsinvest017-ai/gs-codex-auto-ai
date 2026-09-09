"""Collect opt-in live harness safety regressions through the standard tests/ CI path."""
import importlib.util
from pathlib import Path

_path = Path(__file__).resolve().parents[2] / 'testcase/test_live.py'
_spec = importlib.util.spec_from_file_location('repository_testcase_live', _path)
_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_module)

test_report_never_passes_when_required_artifact_is_missing = _module.test_report_never_passes_when_required_artifact_is_missing
test_default_plan_never_starts_provider = _module.test_default_plan_never_starts_provider
test_execute_bootstrap_contains_real_engine_and_planned_route_before_spawn = _module.test_execute_bootstrap_contains_real_engine_and_planned_route_before_spawn
