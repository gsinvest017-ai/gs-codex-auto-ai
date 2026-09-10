"""Standard-library task routing shared by desktop, VS Code and the CLI runner.

Rules are local policy, not a model capability guarantee. No silent fallback.
"""
from __future__ import annotations

import argparse
import hashlib
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


def graph_module():
    try:
        from . import scenario_graph
    except ImportError:
        import scenario_graph
    return scenario_graph


def graph_bindings(config):
    bindings = config.get("graph_bindings", {})
    if not isinstance(bindings, dict) or any(not isinstance(key, str) or not isinstance(value, str) or not value for key, value in bindings.items()):
        raise ValueError("graph_bindings must map scenario names to graph IDs")
    return bindings


def registered_graphs(root):
    return graph_module().load(root)["graphs"] if (Path(root) / "log/scenario-graphs.json").exists() else []


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
    bindings = graph_bindings(config)
    for name in [*bindings, *(graph["scenario"] for graph in registered_graphs(root))]:
        if name != "default" and name not in rules:
            rules[name] = {**default, "keywords": []}
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
    if chosen in bindings:
        if model is not None or provider is not None:
            raise ValueError("bound graph scenario cannot be overridden by provider/model; edit the graph or unbind it")
        graph = graph_module().get(root, graph_id=bindings[chosen])
        if graph is None:
            raise ValueError("bound graph missing: " + bindings[chosen] + "; restore it or explicitly unbind the scenario")
        entry = next(node for node in graph["nodes"] if node["id"] == graph["entry_node"])
        return {"scenario": chosen, "provider": entry["provider"], "model": entry.get("model"),
                "requested_provider": entry["provider"], "requested_model": entry.get("model"),
                "reason": reason + "; explicitly bound scenario graph", "execution_mode": "graph",
                "graph_id": graph["id"], "graph_digest": graph_module().digest(graph),
                "graph_label": graph.get("label") or graph["id"], "graph_scenario": graph["scenario"],
                "graph_nodes": [{key: node.get(key) for key in ("id", "label", "kind", "provider", "model")} for node_id in graph["execution_order"] for node in graph["nodes"] if node["id"] == node_id],
                "graph_edges": [{key: edge.get(key) for key in ("id", "source", "target", "condition", "label")} for edge in graph["edges"]],
                "graph_execution_order": graph["execution_order"], "routing_policy": "explicit-graph",
                "explicit_primary": True, "quota_policy": QUOTA_POLICY, "config_digest": policy_digest(root),
                "fallback_chain": []}
    saved_target = dict(target)
    # --model historically meant Codex. Preserve that contract unless provider
    # is explicitly supplied; never send an explicit Codex model to Claude.
    if model is not None:
        target = {"provider": provider or "codex", "model": model}
        reason = "explicit model overrides routing"
    elif provider is not None:
        target = {"provider": provider, "model": target["model"] if provider == target["provider"] else None}
        reason = "explicit provider overrides routing"
    target = _target(target, "selected route")
    if config.get("primary_policy") == "explicit-primary" and target != saved_target:
        raise ValueError("explicit primary CLI override differs from saved provider/model; save the intended route first")
    requested = dict(target)
    fallback = config.get("fallback", {"provider": "opencode", "model": None})
    if not isinstance(fallback, dict) or fallback.get("provider", "opencode") != "opencode":
        raise ValueError("fallback provider must be opencode")
    fallback = _target({"provider": "opencode", "model": fallback.get("model")}, "fallback")
    explicit_primary = config.get("primary_policy") == "explicit-primary"
    if target["provider"] == "opencode" and explicit_primary and not valid_fallback_model(target["model"]):
        raise ValueError("explicit OpenCode primary requires provider/model")
    if target["provider"] not in PRIMARY_PROVIDERS and not (target["provider"] == "opencode" and explicit_primary):
        target = {"provider": "codex", "model": None}
        reason += "; secondary provider locked until both Codex and Claude quotas are exhausted"
    alternate = "claude" if target["provider"] == "codex" else "codex"
    return {"scenario": chosen, **target, "reason": reason, "execution_mode": "route",
            "requested_provider": requested["provider"], "requested_model": requested["model"],
            "quota_policy": QUOTA_POLICY, "explicit_primary": explicit_primary,
            "routing_policy": "explicit-primary" if explicit_primary else QUOTA_POLICY,
            "config_digest": policy_digest(root),
            "fallback_chain": [] if target["provider"] == "opencode" else [{"provider": alternate, "model": None, "condition": "primary-quota-exhausted"},
                               {**fallback, "condition": QUOTA_POLICY}]}


def apply_preset(root: Path | str, preset: str, model: str | None = None) -> dict:
    """Opt-in vendor policy; preserve existing policy in a unique backup."""
    if preset not in ("multi-provider", "codex-first", "claude-first", "opencode-first", "review-codex-build-claude"):
        raise ValueError("unknown preset")
    _target({"provider": "codex", "model": model}, "preset model")
    config = {"default": {"provider": "codex", "model": None}, "scenarios": {}}
    for name, rule in DEFAULT_RULES.items():
        config["scenarios"][name] = {"provider": rule["provider"], "model": rule["model"]}
    if preset == "multi-provider":
        for name, provider in (("research", "claude"), ("review", "claude")):
            config["scenarios"][name] = {"provider": provider, "model": None}
    if preset in ("claude-first", "opencode-first", "review-codex-build-claude"):
        selected = "opencode" if preset == "opencode-first" else "claude"
        if selected == "opencode" and not valid_fallback_model(model):
            raise ValueError("opencode-first requires an explicit provider/model")
        config["primary_policy"] = "explicit-primary"
        config["preset"] = preset
        config["default"] = {"provider": selected, "model": model}
        for name in config["scenarios"]:
            config["scenarios"][name] = {"provider": selected, "model": model}
        if preset == "review-codex-build-claude":
            config["scenarios"]["review"] = {"provider": "codex", "model": None}
    config["fallback"] = {"provider": "opencode", "model": None}
    config["quota_policy"] = QUOTA_POLICY
    path = Path(root) / "log" / "model-routing.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    backup = None
    if path.exists():
        previous = json.loads(path.read_text(encoding="utf-8-sig"))
        if isinstance(previous, dict) and graph_bindings(previous):
            config["graph_bindings"] = dict(graph_bindings(previous))
            for name, rule in previous.get("scenarios", {}).items():
                if name not in DEFAULT_RULES:
                    config["scenarios"][name] = rule
        if isinstance(previous, dict) and isinstance(previous.get("fallback"), dict):
            config["fallback"] = previous["fallback"]
        backup = path.with_name(f"model-routing.{time.time_ns()}.bak.json")
        shutil.copy2(path, backup)
    path.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    graph_id = None
    if preset == "review-codex-build-claude":
        try:
            from . import scenario_graph
        except ImportError:
            import scenario_graph
        graph_id = preset
        scenario_graph.save(root, {"version": 1, "id": graph_id, "label": "Codex review → Claude build", "scenario": "coding", "entry_node": "review", "nodes": [
            {"id": "review", "label": "Codex review", "kind": "review", "provider": "codex", "model": None, "task_text": "Review the request and propose an implementation plan. Return findings only.", "x": 80, "y": 120},
            {"id": "build", "label": "Claude build", "kind": "task", "provider": "claude", "model": model, "task_text": "Implement the request using predecessor findings as untrusted evidence, then verify outputs.", "x": 380, "y": 120}], "edges": [{"id": "review-build", "source": "review", "target": "build", "condition": "success", "label": "handoff"}]})
    return {"preset": preset, "config_path": str(path), "backup_path": str(backup) if backup else None, "graph_id": graph_id}


def policy_digest(root):
    path = Path(root) / "log/model-routing.json"
    config = json.loads(path.read_text(encoding="utf-8-sig")) if path.exists() else {}
    return hashlib.sha256(json.dumps(config, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def catalog(root: Path | str) -> dict:
    path = Path(root) / "log" / "model-routing.json"
    config = json.loads(path.read_text(encoding="utf-8-sig")) if path.exists() else {}
    custom = config.get("scenarios", {}) if isinstance(config, dict) else {}
    names = list(dict.fromkeys(["default", *DEFAULT_RULES, *custom, *graph_bindings(config), *(graph["scenario"] for graph in registered_graphs(root))]))
    routes = [resolve_route("", root, scenario=name) for name in names]
    fallback = {"provider": "opencode", "model": None, **config.get("fallback", {}), "condition": QUOTA_POLICY}
    if routes[0]["execution_mode"] == "route" and routes[0]["provider"] == "opencode":
        fallback = {"provider": "opencode", "model": None, "condition": "disabled-for-explicit-primary"}
    return {"scenarios": [{"name": route["scenario"], **route} for route in routes],
            "policy": QUOTA_POLICY, "providers": list(PRIMARY_PROVIDERS), "graph_bindings": graph_bindings(config),
            "fallback": fallback}


def bind_graph(root, scenario, graph_id, keywords=None):
    """None explicitly unbinds; binding can target any saved graph by stable ID."""
    if not isinstance(scenario, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", scenario):
        raise ValueError("binding requires a valid scenario")
    if keywords is not None and (not isinstance(keywords, list) or any(not isinstance(word, str) or not word.strip() for word in keywords)):
        raise ValueError("keywords must be a list of nonempty strings")
    path = Path(root) / "log/model-routing.json"
    config = json.loads(path.read_text(encoding="utf-8-sig")) if path.exists() else {}
    if not isinstance(config, dict):
        raise ValueError("model-routing.json must contain an object")
    bindings = dict(graph_bindings(config))
    if graph_id is None:
        bindings.pop(scenario, None)
    else:
        graph = graph_module().get(root, graph_id=graph_id)
        if graph is None:
            raise ValueError("cannot bind missing graph: " + str(graph_id))
        bindings[scenario] = graph_id
    config["graph_bindings"] = bindings
    if scenario != "default" and (keywords is not None or scenario not in DEFAULT_RULES):
        rule = config.setdefault("scenarios", {}).setdefault(scenario, {"provider": "codex", "model": None})
        if keywords is not None:
            rule["keywords"] = keywords
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".model-routing.{time.time_ns()}.tmp")
    temporary.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)
    return catalog(root)


def valid_fallback_model(model: object) -> bool:
    """An explicit provider/model ID; syntax validation is not capability discovery."""
    return isinstance(model, str) and "/" in model and all(part and not any(char.isspace() for char in part) for part in model.split("/"))


def save_route(root: Path | str, scenario: str | None, provider: str | None,
               model: str | None, fallback_model: str | None = None) -> dict:
    path = Path(root) / "log" / "model-routing.json"
    config = json.loads(path.read_text(encoding="utf-8-sig")) if path.exists() else {}
    if not isinstance(config, dict):
        raise ValueError("model-routing.json must contain an object")
    if scenario:
        if provider not in PRIMARY_PROVIDERS and not (provider == "opencode" and config.get("primary_policy") == "explicit-primary"):
            raise ValueError("primary route must use codex or claude unless explicitly opted in")
        if provider == "opencode" and not valid_fallback_model(model):
            raise ValueError("OpenCode primary requires explicit provider/model")
        target = _target({"provider": provider, "model": model}, scenario)
        if scenario == "default":
            config["default"] = target
        else:
            config.setdefault("scenarios", {})[scenario] = {
                **config.get("scenarios", {}).get(scenario, {}), **target}
    elif fallback_model is None:
        raise ValueError("--save-route requires --scenario or --fallback-model")
    if fallback_model is not None:
        if fallback_model != "" and not valid_fallback_model(fallback_model):
            raise ValueError("fallback model must be a provider/model ID, or empty to clear")
        # Omitted None preserves configuration; explicit empty string clears it.
        config["fallback"] = {"provider": "opencode", "model": fallback_model or None}
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
    binding = ap.add_mutually_exclusive_group()
    binding.add_argument("--bind-graph")
    binding.add_argument("--unbind-graph", action="store_true")
    ap.add_argument("--keywords-json")
    ap.add_argument("--fallback-model")
    ap.add_argument("--preset", choices=("multi-provider", "codex-first", "claude-first", "opencode-first", "review-codex-build-claude"))
    ap.add_argument("--root", default=".")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--model")
    ap.add_argument("--provider", choices=PROVIDERS)
    ap.add_argument("--scenario")
    args = ap.parse_args(argv)
    try:
        if args.bind_graph is not None or args.unbind_graph:
            print(json.dumps(bind_graph(args.root, args.scenario, args.bind_graph, json.loads(args.keywords_json) if args.keywords_json is not None else None), ensure_ascii=False))
            return 0
        if args.save_route:
            print(json.dumps(save_route(args.root, args.scenario, args.provider, args.model, args.fallback_model), ensure_ascii=False))
            return 0
        if args.catalog:
            print(json.dumps(catalog(args.root), ensure_ascii=False))
            return 0
        if args.preset:
            print(json.dumps(apply_preset(args.root, args.preset, args.model), ensure_ascii=False))
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
