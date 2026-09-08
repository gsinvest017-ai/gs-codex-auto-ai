"""Standard-library task routing shared by desktop, VS Code and the CLI runner.

Rules are local policy, not a model capability guarantee. No silent fallback.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
import re
from pathlib import Path

PROVIDERS = ("codex", "claude", "gemini", "deepseek", "opencode")
PRIMARY_PROVIDERS = ("codex", "claude")
QUOTA_POLICY = "both-primary-exhausted"
DEFAULT_RULES = {
    "3d_modeling": {"provider": "codex", "model": "gpt-6-astra", "keywords": [
        "3d", "blender", "mesh", "三維", "建模"]},
    "debugging": {"provider": "codex", "model": None, "keywords": [
        "debug", "debugging", "bug", "除錯", "修復"]},
    "review": {"provider": "codex", "model": None, "keywords": [
        "review", "audit", "審查", "審計"]},
    "research": {"provider": "codex", "model": None, "keywords": [
        "research", "investigate", "研究", "調查"]},
    "documents": {"provider": "codex", "model": None, "keywords": [
        "document", "documentation", "文書", "文件", "報告"]},
    "coding": {"provider": "codex", "model": None, "keywords": [
        "implement", "coding", "code", "程式", "實作"]},
}
# Negated mentions must not dispatch an unrelated task to a specialist. Split
# clauses first so 'no Blender; create a 3D mesh' still finds the positive task.
NEGATION = re.compile(r"\b(?:no|not|without|avoid|never|don't|do not)\b|不要|不需|不用|無需|並非|不是", re.I)
NON_SPATIAL = re.compile(r"machine learning|\bml\b|financial|statistical|data model|機器學習|統計|金融|資料|數據|數學|mathematical|tensor|張量", re.I)


def _matched(prompt: str, keywords: list[str]) -> str | None:
    for clause in re.split(r"[,.!?;\n，。！？；]|\bbut\b|但是|但要", prompt, flags=re.I):
        if NEGATION.search(clause):
            continue
        for word in keywords:
            if word in ("建模", "3d", "三維") and NON_SPATIAL.search(clause):
                continue
            pattern = re.escape(word)
            if word.isascii() and word.replace(" ", "").isalnum():
                pattern = r"(?<![a-zA-Z0-9_])" + pattern + r"(?![a-zA-Z0-9_])"
            if re.search(pattern, clause, re.I):
                return word
    return None


def _target(value: object, label: str) -> dict:
    if not isinstance(value, dict) or value.get("provider") not in PROVIDERS:
        raise ValueError(f"{label}: provider must be one of {PROVIDERS}")
    model = value.get("model")
    if model is not None and (not isinstance(model, str) or not model.strip()):
        raise ValueError(f"{label}: model must be a nonempty string or null")
    return {"provider": value["provider"], "model": model}


def resolve_route(prompt: str, root: Path | str = ".", *, model: str | None = None,
                  provider: str | None = None, scenario: str | None = None) -> dict:
    """Resolve policy only; never invokes a provider or probes credentials."""
    path = Path(root) / "log" / "model-routing.json"
    config = json.loads(path.read_text(encoding="utf-8-sig")) if path.exists() else {}
    if not isinstance(config, dict):
        raise ValueError("model-routing.json must contain an object")
    default = _target(config.get("default", {"provider": "codex", "model": None}), "default")
    overrides = config.get("scenarios", {})
    if not isinstance(overrides, dict):
        raise ValueError("scenarios must be an object")
    # Overrides inherit built-in keywords; new rules must provide their own
    # keywords, or can be selected explicitly using --scenario.
    rules = {name: {**rule, **overrides.get(name, {})}
             for name, rule in DEFAULT_RULES.items()
             if isinstance(overrides.get(name, {}), dict)}
    for name, rule in overrides.items():
        if not isinstance(rule, dict):
            raise ValueError(f"{name}: scenario must be an object")
        if name not in rules:
            rules[name] = rule
    for name, rule in rules.items():
        _target(rule, name)
        priority = rule.get("priority", 0)
        if not isinstance(priority, (int, float)) or isinstance(priority, bool):
            raise ValueError(f"{name}: priority must be a number")
        keywords = rule.get("keywords", [])
        if not isinstance(keywords, list) or any(not isinstance(k, str) or not k.strip() for k in keywords):
            raise ValueError(f"{name}: keywords must be nonempty strings")
    chosen, target, reason = "default", default, "default provider configuration"
    if scenario:
        if scenario != "default" and scenario not in rules:
            raise ValueError(f"unknown scenario: {scenario}")
        chosen = scenario
        target = default if scenario == "default" else _target(rules[scenario], scenario)
        reason = "explicit scenario"
    else:
        # Leading task intent beats a tool mention, unlike trailing documentation.
        intent = re.match(r"^\s*(?:(?:please|請|幫我)\s*)?(review|audit|research|investigate|debug|debugging|審查|審計|研究|調查|除錯|修復)", prompt, re.I)
        intent_text = intent.group(1).lower() if intent else ""
        def rank(item):
            name, rule = item
            return (rule.get("priority", 0), bool(intent_text and intent_text in rule.get("keywords", [])))
        for name, rule in sorted(rules.items(), key=rank, reverse=True):
            hit = _matched(prompt, rule.get("keywords", []))
            if hit:
                chosen, target, reason = name, _target(rule, name), f"matched keyword: {hit}"
                break
    # --model historically meant Codex. Preserve that contract unless provider
    # is explicitly supplied; never send an explicit Codex model to Claude.
    if model is not None:
        target = {"provider": provider or "codex", "model": model}
        reason = "explicit model overrides routing"
    elif provider is not None:
        target = {"provider": provider, "model": target["model"] if provider == target["provider"] else None}
        reason = "explicit provider overrides routing"
    target = _target(target, "selected route")
    requested = dict(target)
    fallback = config.get("fallback", {"provider": "opencode", "model": None})
    if not isinstance(fallback, dict) or fallback.get("provider", "opencode") != "opencode":
        raise ValueError("fallback provider must be opencode")
    fallback = _target({"provider": "opencode", "model": fallback.get("model")}, "fallback")
    if target["provider"] not in PRIMARY_PROVIDERS:
        target = {"provider": "codex", "model": None}
        reason += "; secondary provider locked until both Codex and Claude quotas are exhausted"
    alternate = "claude" if target["provider"] == "codex" else "codex"
    return {"scenario": chosen, **target, "reason": reason,
            "requested_provider": requested["provider"], "requested_model": requested["model"],
            "quota_policy": QUOTA_POLICY,
            "fallback_chain": [{"provider": alternate, "model": None, "condition": "primary-quota-exhausted"},
                               {**fallback, "condition": QUOTA_POLICY}]}


def apply_preset(root: Path | str, preset: str) -> dict:
    """Opt-in vendor policy; preserve existing policy in a unique backup."""
    if preset not in ("multi-provider", "codex-first"):
        raise ValueError("unknown preset")
    config = {"default": {"provider": "codex", "model": None}, "scenarios": {}}
    for name, rule in DEFAULT_RULES.items():
        config["scenarios"][name] = {"provider": rule["provider"], "model": rule["model"]}
    if preset == "multi-provider":
        for name, provider in (("research", "claude"), ("review", "claude")):
            config["scenarios"][name] = {"provider": provider, "model": None}
    config["fallback"] = {"provider": "opencode", "model": None}
    config["quota_policy"] = QUOTA_POLICY
    path = Path(root) / "log" / "model-routing.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    backup = None
    if path.exists():
        previous = json.loads(path.read_text(encoding="utf-8-sig"))
        if isinstance(previous, dict) and isinstance(previous.get("fallback"), dict):
            config["fallback"] = previous["fallback"]
        backup = path.with_name(f"model-routing.{time.time_ns()}.bak.json")
        shutil.copy2(path, backup)
    path.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"preset": preset, "config_path": str(path), "backup_path": str(backup) if backup else None}


def catalog(root: Path | str) -> dict:
    path = Path(root) / "log" / "model-routing.json"
    config = json.loads(path.read_text(encoding="utf-8-sig")) if path.exists() else {}
    custom = config.get("scenarios", {}) if isinstance(config, dict) else {}
    names = list(dict.fromkeys(["default", *DEFAULT_RULES, *custom]))
    routes = [resolve_route("", root, scenario=name) for name in names]
    return {"scenarios": [{"name": route["scenario"], **route} for route in routes],
            "policy": QUOTA_POLICY, "providers": list(PRIMARY_PROVIDERS),
            "fallback": routes[0]["fallback_chain"][-1]}


def save_route(root: Path | str, scenario: str | None, provider: str | None,
               model: str | None, fallback_model: str | None = None) -> dict:
    path = Path(root) / "log" / "model-routing.json"
    config = json.loads(path.read_text(encoding="utf-8-sig")) if path.exists() else {}
    if not isinstance(config, dict):
        raise ValueError("model-routing.json must contain an object")
    if scenario:
        if provider not in PRIMARY_PROVIDERS:
            raise ValueError("primary route must use codex or claude")
        target = _target({"provider": provider, "model": model}, scenario)
        if scenario == "default":
            config["default"] = target
        else:
            config.setdefault("scenarios", {})[scenario] = {
                **config.get("scenarios", {}).get(scenario, {}), **target}
    elif fallback_model is None:
        raise ValueError("--save-route requires --scenario or --fallback-model")
    if fallback_model is not None:
        if not fallback_model.strip() or "/" not in fallback_model:
            raise ValueError("fallback model must be a provider/model ID")
        config["fallback"] = {"provider": "opencode", "model": fallback_model}
    config["quota_policy"] = QUOTA_POLICY
    path.parent.mkdir(parents=True, exist_ok=True)
    # Atomic replacement prevents preview readers seeing partial JSON.
    temporary = path.with_name(f".model-routing.{time.time_ns()}.tmp")
    temporary.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)
    return catalog(root)


def main(argv=None) -> int:
    # Node execFile decodes stdout as UTF-8 even on Windows with a cp950 locale.
    # Keep JSON output encoding stable for every caller, including preset/errors.
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--prompt")
    ap.add_argument("--catalog", action="store_true")
    ap.add_argument("--save-route", action="store_true")
    ap.add_argument("--fallback-model")
    ap.add_argument("--preset", choices=("multi-provider", "codex-first"))
    ap.add_argument("--root", default=".")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--model")
    ap.add_argument("--provider", choices=PROVIDERS)
    ap.add_argument("--scenario")
    args = ap.parse_args(argv)
    try:
        if args.save_route:
            print(json.dumps(save_route(args.root, args.scenario, args.provider, args.model, args.fallback_model), ensure_ascii=False))
            return 0
        if args.catalog:
            print(json.dumps(catalog(args.root), ensure_ascii=False))
            return 0
        if args.preset:
            print(json.dumps(apply_preset(args.root, args.preset), ensure_ascii=False))
            return 0
        if args.prompt is None:
            raise ValueError("--prompt is required unless --preset is supplied")
        route = resolve_route(args.prompt, args.root, model=args.model, provider=args.provider, scenario=args.scenario)
    except (OSError, ValueError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps(route, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
