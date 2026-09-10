"""Collect repository testcase matrix in the existing tests/ CI entry point."""
import importlib.util
from pathlib import Path

_path = Path(__file__).resolve().parents[2] / 'testcase/test_matrix.py'
_spec = importlib.util.spec_from_file_location('repository_testcase_matrix', _path)
_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_module)
test_complete_cartesian_matrix = _module.test_complete_cartesian_matrix
test_oracle_quota_and_fatal_truth_table = _module.test_oracle_quota_and_fatal_truth_table
test_fallback_model_boundaries_and_explicit_clear = _module.test_fallback_model_boundaries_and_explicit_clear
test_all_prompt_cases_inferred_under_all_presets = _module.test_all_prompt_cases_inferred_under_all_presets
