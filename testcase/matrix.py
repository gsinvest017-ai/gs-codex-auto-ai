"""Finite equivalence-class routing matrix. No real provider can be launched.

Run: python testcase/matrix.py --output testcase/reports/matrix
The synthetic model IDs are test fixtures, not a live capability catalogue.
"""
from __future__ import annotations
import argparse
from collections import Counter
import itertools
import math
import hashlib
from datetime import datetime, timezone
import subprocess
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools import model_router as router
from tools import codex_runner as runner
from tools.events_model import routing_stats

DIMENSIONS = {
    "nonstop": [False, True],
    "preset": ["codex-first", "multi-provider", "claude-first", "opencode-first", "review-codex-build-claude"],
    "scenario": ["default", "3d_modeling", "debugging", "review", "research", "documents", "coding"],
    "primary": ["codex", "claude", "opencode"],
    "model_class": ["blank", "explicit_valid", "mismatch", "invalid"],
    "fallback_class": ["blank", "Gemini", "DeepSeek", "other", "invalid"],
}
OUTCOMES = ["primaryok", "onequota", "bothquota", "genericerror"]
FALLBACKS = {"blank": None, "Gemini": "google/gemini-matrix-test", "DeepSeek": "deepseek/deepseek-matrix-test", "other": "vendor/other-matrix-test", "invalid": "missing-provider-prefix"}
PROMPT = "Controlled matrix fixture.\nSecond line: preserve quotes \" and $ literally."


def prompt_cases():
    return json.loads((Path(__file__).parent / "cases.json").read_text(encoding="utf-8-sig"))["cases"]


def canonical_prompt(scenario):
    case = next(case for case in prompt_cases() if case["expected_scenario"] == scenario)
    return (Path(__file__).parent / case["prompt_file"]).read_text(encoding="utf-8-sig")


def combinations():
    for index, values in enumerate(itertools.product(*DIMENSIONS.values())):
        parameters = dict(zip(DIMENSIONS, values))
        fixture = next(case for case in prompt_cases() if case["expected_scenario"] == parameters["scenario"])
        yield {"id": f"matrix-{index:04d}", "parameters": parameters,
               "case_id": fixture["id"], "prompt_file": fixture["prompt_file"],
               "routing_text": fixture.get("routing_text"),
               "expected_scenario": fixture["expected_scenario"],
               "expected_primary_after_override": parameters["primary"],
               "expected_artifacts": fixture["expected_artifacts"],
               "artifact_expectation_scope": "manual_or_live_acceptance_not_controlled_matrix",
               "model_representative": model_value(parameters),
               "fallback_representative": FALLBACKS[parameters["fallback_class"]]}


EXPLICIT_PRESETS = {"claude-first", "opencode-first", "review-codex-build-claude"}
PRESET_OPENCODE_MODEL = "vendor/preset-matrix-test"


def expected_preset_primary(preset, scenario):
    if preset == "opencode-first":
        return "opencode"
    if preset == "claude-first" or preset == "review-codex-build-claude" and scenario != "review":
        return "claude"
    if preset == "multi-provider" and scenario in ("review", "research"):
        return "claude"
    return "codex"


def apply_test_preset(workspace, preset):
    return router.apply_preset(workspace, preset, PRESET_OPENCODE_MODEL if preset == "opencode-first" else None)


def expected_combination_count():
    return math.prod(len(values) for values in DIMENSIONS.values())


def model_value(parameters):
    kind, primary = parameters["model_class"], parameters["primary"]
    if primary == "opencode":
        return {"blank": None, "explicit_valid": "vendor/primary-matrix-test", "mismatch": "vendor/unsupported-matrix-test", "invalid": "   "}[kind]
    return {"blank": None, "explicit_valid": primary + "-matrix-test", "mismatch": ("claude" if primary == "codex" else "codex") + "-matrix-test", "invalid": "   "}[kind]


def check(identifier, expected, actual):
    return {"id": identifier, "status": "passed" if expected == actual else "failed", "expected": expected, "actual": actual}


def oracle(parameters, outcome):
    """Requirement truth table, independent of resolver/runner implementations."""
    primary = parameters["primary"]
    secondary = "claude" if primary == "codex" else "codex"
    if parameters["model_class"] == "invalid" or parameters["fallback_class"] == "invalid" or (primary == "opencode" and (parameters["preset"] not in EXPLICIT_PRESETS or parameters["model_class"] == "blank")):
        return {"providers": [], "ok": False, "configuration_rejected": True}
    if parameters["model_class"] == "mismatch" or outcome == "genericerror":
        return {"providers": [primary], "ok": False, "configuration_rejected": False}
    if outcome == "primaryok":
        return {"providers": [primary], "ok": True, "configuration_rejected": False}
    if primary == "opencode":
        return {"providers": [primary], "ok": False, "configuration_rejected": False}
    if outcome == "onequota":
        return {"providers": [primary, secondary], "ok": True, "configuration_rejected": False}
    fallback = parameters["fallback_class"] != "blank"
    return {"providers": [primary, secondary] + (["opencode"] if fallback else []), "ok": fallback, "configuration_rejected": False}


def run_case(case, workspace):
    parameters = case["parameters"]
    workspace.mkdir(parents=True, exist_ok=True)
    apply_test_preset(workspace, parameters["preset"])
    initial = router.resolve_route("", workspace, scenario=parameters["scenario"])
    preset_primary = expected_preset_primary(parameters["preset"], parameters["scenario"])
    checks = [check("preset_primary_before_override", preset_primary, initial["provider"]), check("preset_policy_before_override", parameters["preset"] in EXPLICIT_PRESETS, initial["explicit_primary"])]
    if parameters["preset"] == "opencode-first":
        checks.append(check("preset_explicit_model", PRESET_OPENCODE_MODEL, initial["model"]))
    config = workspace / "log/model-routing.json"
    before = config.read_bytes()
    model = model_value(parameters)
    fallback = FALLBACKS[parameters["fallback_class"]]
    error = None
    try:
        router.save_route(workspace, parameters["scenario"], parameters["primary"], model, fallback)
    except ValueError as exc:
        error = str(exc)
    rejected = oracle(parameters, "primaryok")["configuration_rejected"]
    checks.append(check("configuration_rejected", rejected, error is not None))
    if error:
        checks.append(check("rejection_preserves_config", True, before == config.read_bytes()))
        route = None
    else:
        route = router.resolve_route(PROMPT, workspace, scenario=parameters["scenario"])
        inferred = router.resolve_route(canonical_prompt(parameters["scenario"]), workspace)
        checks += [check("prompt_inferred_scenario", parameters["scenario"], inferred["scenario"]), check("prompt_inferred_primary", parameters["primary"], inferred["provider"]), check("saved_primary", parameters["primary"], route["provider"]), check("saved_model_exact", model, route["model"]), check("saved_fallback_exact", fallback, json.loads(config.read_text(encoding="utf-8"))["fallback"]["model"]), check("scenario", parameters["scenario"], route["scenario"])]
    if route:
        checks += [check("resolved_explicit_policy", parameters["preset"] in EXPLICIT_PRESETS, route["explicit_primary"]), check("opencode_primary_has_no_implicit_fallback", parameters["primary"] != "opencode" or not route["fallback_chain"], True)]
    executions = []
    for outcome in OUTCOMES:
        events, calls, argv_checks = [], [], []
        run_id = case["id"] + "-" + outcome
        def record(cwd, event):
            events.append({"type": "model_attempt", **event})
        def fake_run(cmd, cwd, expects, grace, heartbeat, **kwargs):
            provider = kwargs["provider"]
            calls.append(provider)
            expected_model = model if len(calls) == 1 else fallback if provider == "opencode" else None
            model_flag = "-m" if provider == "codex" else "--model"
            actual_model = cmd[cmd.index(model_flag) + 1] if model_flag in cmd else None
            argv_checks.extend([check(f"argv_model_{len(calls)}", expected_model, actual_model), check(f"multiline_prompt_{len(calls)}", True, PROMPT in cmd)])
            kwargs["result_metadata"].update({"actual_model": expected_model or provider + "-controlled-default", "usage": {"input_tokens": 11, "output_tokens": 3, "cached_input_tokens": None}, "usage_source": "controlled_fixture"})
            if parameters["model_class"] == "mismatch":
                return False, "fatal:model_not_supported controlled rejection; availability not inferred"
            if outcome == "genericerror":
                return False, "controlled transient error, no quota evidence"
            if outcome == "bothquota" and (provider in ("codex", "claude") or parameters["primary"] == "opencode") or outcome == "onequota" and len(calls) == 1:
                return False, "quota_exhausted:controlled explicit subscription exhaustion"
            return True, "controlled successful provider response"
        expected = oracle(parameters, outcome)
        if route is None:
            ok, reason = False, "configuration rejected: " + str(error)
        else:
            args = SimpleNamespace(codex_cmd=None, dispatcher=False, retries=1, session_grace=1, heartbeat=1, timeout=10, retry_backoff=0)
            # Popen tripwire ensures a future implementation cannot spend real quota.
            with patch.object(runner, "run_once", fake_run), patch.object(runner, "record_attempt", record), patch.object(runner, "resolve_codex", lambda provider: ["controlled-" + provider]), patch.object(runner.shutil, "which", return_value="controlled-executable"), patch.object(runner.subprocess, "Popen", side_effect=AssertionError("real process forbidden")), patch.dict(runner.os.environ, {"CODEXAUTOAI_PARENT_RUN_ID": run_id}):
                ok, reason, metadata, actual = runner.execute_with_fallback(route, PROMPT, workspace, args, run_id, [0], [])
        terminals = [event for event in events if event["outcome"] != "started"]
        execution_checks = [check("provider_sequence", expected["providers"], calls), check("success", expected["ok"], ok), check("terminal_count", len(calls), len(terminals)), check("requested_provider_evidence", [parameters["primary"]] * len(calls), [event["requested_provider"] for event in terminals]), check("actual_provider_evidence", calls, [event["actual_provider"] for event in terminals]), check("usage_evidence", [11] * len(calls), [event["usage"]["input_tokens"] for event in terminals]), check("opencode_explicit_or_quota_authorization", True, all((parameters["primary"] == "opencode" and parameters["preset"] in EXPLICIT_PRESETS and event.get("routing_policy") == "explicit-primary" and bool(event.get("config_digest"))) or set(event["quota_exhausted_providers"]) == {"codex", "claude"} for event in events if event["actual_provider"] == "opencode"))] + argv_checks
        metrics = routing_stats(events, run_id=run_id)
        expected_counts = Counter(expected["providers"])
        execution_checks += [check("metrics_policy_violations", [], metrics["violations"]), check("metrics_provider_calls", dict(expected_counts), {provider: value["attempts"] for provider, value in metrics["providers"].items()}), check("metrics_input_totals", {provider: 11 * count for provider, count in expected_counts.items()}, {provider: value["inTok"] for provider, value in metrics["providers"].items()}), check("metrics_output_totals", {provider: 3 * count for provider, count in expected_counts.items()}, {provider: value["outTok"] for provider, value in metrics["providers"].items()}), check("metrics_unknown_cache_and_cost", True, all(value["cacheTok"] is None and value["cost"] is None for value in metrics["providers"].values()))]
        executions.append({"outcome": outcome, "run_id": run_id, "canonical_metrics": metrics, "checks": execution_checks, "expected": expected, "actual": {"providers": calls, "ok": ok, "reason": reason}, "events": events})
    return {**case, "representatives": {"model": model, "fallback": fallback, "prompt": PROMPT}, "checks": checks, "executions": executions, "nonstop_verification": {"routing_invariance": "covered", "command_prefix": "external_evidence_audit", "runtime_continuation": "NOT_VERIFIED"}}


def run_matrix(output=None):
    generated_at = datetime.now(timezone.utc).isoformat()
    try:
        revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, encoding="utf-8").strip()
    except (OSError, subprocess.SubprocessError):
        revision = None
    evidence = {"generated_at": generated_at, "revision": revision,
                "source_sha256": {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in ("testcase/matrix.py", "testcase/cases.json", "tools/model_router.py", "tools/codex_runner.py", "tools/events_model.py")},
                "scope": {"parameters": "Finite equivalence-class Cartesian product, not every arbitrary string. OpenCode-first preset is initialized with a synthetic explicit provider/model; primary override blank model cases verify missing-model rejection. OpenCode primary quota cases never silently switch providers.",
                          "model_validation": "Synthetic IDs; mismatch is controlled fatal runtime rejection, not a discovered capability.",
                          "invalid_primary_model": "Whitespace is rejected by direct save_route API. UI trims whitespace to blank/null; UI normalization is separately audited.",
                          "routing_metrics": f"All {expected_combination_count()} parameter and {expected_combination_count() * len(OUTCOMES)} outcome combinations use production core with controlled providers. Illegal provider/model configurations are rejected before calls.",
                          "prompts": f"Canonical full prompts are inferred for valid configurations; all {len(prompt_cases())} short/full prompt fixtures are separately classified under all {len(DIMENSIONS['preset'])} presets.",
                          "activity_artifacts_phase_history": "Representative evidence audits and separate live smoke only; matrix provider outcomes are controlled, not real generations. review-codex-build-claude preset routing is covered here; its multi-node execution is verified separately by scenario_graph tests.",
                          "mode": "Routing invariance plus separate UI command-prefix audit; runtime continuation NOT_VERIFIED."}}
    cases = []
    with tempfile.TemporaryDirectory(prefix="codexautoai-matrix-") as directory:
        root = Path(directory).resolve()
        for case in combinations():
            cases.append(run_case(case, root / case["id"]))
    counts = Counter(check["status"] for case in cases for check in case["checks"] + [check for execution in case["executions"] for check in execution["checks"]])
    report = {"schema_version": 1, **evidence, "source": "controlled-provider-matrix", "dimensions": DIMENSIONS, "quota_outcomes": OUTCOMES, "coverage": {"parameter_combinations": len(cases), "execution_combinations": sum(len(case["executions"]) for case in cases), "checks": dict(counts), "paid_calls": 0, "runtime_continuation": "NOT_VERIFIED", "model_availability": "NOT_VERIFIED"}, "cases": cases}
    if output:
        output = Path(output)
        output.mkdir(parents=True, exist_ok=True)
        (output / "manifest.json").write_text(json.dumps({"schema_version": 1, **evidence, "dimensions": DIMENSIONS, "quota_outcomes": OUTCOMES, "cases": list(combinations())}, indent=2) + "\n", encoding="utf-8")
        (output / "summary.json").write_text(json.dumps({key: value for key, value in report.items() if key != "cases"}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    result = run_matrix(parser.parse_args().output)
    print(json.dumps(result["coverage"]))
    raise SystemExit(bool(result["coverage"]["checks"].get("failed")))
