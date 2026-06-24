"""orchestrator.py — CodexAutoAI v2 deterministic control-flow shell.

Implements ORCH-R1: all loops, branching, and termination are driven by this
deterministic code — NOT by an LLM. LLM work is confined to injected callables
(`produce_fix`, `review`) whose outputs are gated here.

This module integrates the v2 capability modules:
  termination · escalation · depgraph · state · audit · events · ownership
  · safety · supplychain · review

The LLM-facing steps are dependency-injected so the control flow is fully
testable without any model call.
"""
from __future__ import annotations

import subprocess
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Callable, Iterator

from .audit import AuditLog
from .depgraph import CycleError, topological_batches
from .escalation import Escalator
from .events import EventBus, redact
from .injection_guard import is_suspicious, sanitize
from .merge_coordinator import MergeCoordinator
from .ownership import partition
from .portman import PortAllocator
from .property_verifier import parse_scenarios
from .property_verifier import verify as verify_properties
from .repo_map import build_map
from .review import select_reviewer_model
from .safety import PermissionPolicy, assert_writable, mode3_authorized
from .sandbox import Sandbox
from .secret_scan import blocks_delivery
from .secret_scan import scan as scan_security
from .state import RunState
from .supplychain import DependencyController
from .syntax_guard import guard_write as syntax_guard_write
from .termination import TerminationController
from .worktree import WorktreeManager


@dataclass
class RunResult:
    status: str                 # 'resolved' | 'resolved_after_replan' | 'escalated'
    iterations: int = 0
    reason: str | None = None
    escalation: object | None = None
    final_defects: list = field(default_factory=list)


class Orchestrator:
    """Deterministic phase/loop driver. The brain (Claude) and writer (Codex)
    plug in as callables; this class owns the control flow and the guards."""

    def __init__(
        self,
        *,
        event_path: str,
        audit_path: str,
        state_path: str,
        run_id: str,
        policy: PermissionPolicy | None = None,
    ) -> None:
        self.events = EventBus(event_path)
        self.audit = AuditLog(audit_path)
        self.state_path = state_path
        self.state = RunState.resume_or_new(state_path, run_id)
        self.escalator = Escalator()
        self.policy = policy or PermissionPolicy()

    # --- OBS-R2/R3: deterministic phase-boundary events ----------------------
    @contextmanager
    def phase(self, phase: str) -> Iterator[None]:
        """Emit exactly one ``phase_start`` and one ``phase_end`` event around a
        phase, no matter what the LLM does inside it.

        Because the deterministic shell (not the LLM) owns these writes, the
        phase progress in ``log/events.jsonl`` — and therefore ``tools/progress.py``
        — is guaranteed to advance even if an agent forgets to log. ``status`` on
        ``phase_end`` is ``"success"`` on normal exit and ``"failure"`` if the
        body raises (the exception still propagates).

        Usage::

            with orch.phase("phase3"):
                ... run the phase ...
        """
        self.state.set_phase(phase)
        self.events.emit("phase_start", phase=phase, status="in_progress")
        self.audit.append({"event": "phase_start", "phase": phase})
        try:
            yield
        except BaseException as exc:  # noqa: BLE001 — record then re-raise
            self.events.emit(
                "phase_end", phase=phase, status="failure",
                error=type(exc).__name__,
            )
            self.audit.append(
                {"event": "phase_end", "phase": phase, "status": "failure"}
            )
            raise
        else:
            self.events.emit("phase_end", phase=phase, status="success")
            self.audit.append(
                {"event": "phase_end", "phase": phase, "status": "success"}
            )

    # --- C11 / SECGOV-R6: MODE3 entry requires out-of-band authorization -----
    def authorize_mode3(self, token: str | None, *, embedded: bool = False) -> bool:
        ok = mode3_authorized(token, embedded=embedded)
        self.events.emit("mode3_auth", granted=ok, embedded=embedded, status="ok")
        self.audit.append({"event": "mode3_auth", "granted": ok, "embedded": embedded})
        return ok

    # --- SAFE-R2 / SECGOV-R5: command + write gates --------------------------
    def guard_command(self, command: str) -> str:
        decision = self.policy.evaluate(command)
        self.events.emit("command_gate", command=command, decision=decision, status="ok")
        return decision

    def guard_write(self, path: str) -> None:
        # raises FrameworkIntegrityError on framework paths (SECGOV-R5)
        assert_writable(path)

    # --- SECGOV-R2: dependency supply-chain gate -----------------------------
    def validate_dependencies(self, deps: list[dict], resolver: Callable | None = None):
        report = DependencyController(resolver=resolver).validate(deps)
        self.events.emit(
            "supplychain_gate",
            ok=report.ok,
            blocked=list(report.blocked),
            status="ok" if report.ok else "blocked",
        )
        return report

    # --- ORCH-R6: parallel build planning (cycle check + ownership) ----------
    def plan_build(self, fns: list[dict]) -> list[list[dict]]:
        graph = {fn["id"]: list(fn.get("deps", [])) for fn in fns}
        topological_batches(graph)  # raises CycleError on a cycle (ORCH-R6)
        batches = partition(fns)     # BUILD-R2 file-ownership partitioning
        self.events.emit("build_plan", batch_count=len(batches), status="ok")
        return batches

    # --- REVIEW-R1: cross-model reviewer selection ---------------------------
    def pick_reviewer(self, fixer_model: str, available: list[str]) -> dict:
        sel = select_reviewer_model(fixer_model, available)
        self.events.emit(
            "reviewer_selected",
            fixer=fixer_model,
            reviewer=sel["reviewer"],
            independent=sel["independent"],
            status="ok",
        )
        return sel

    # --- SECGOV-R1 / C10: untrusted requirement intake -----------------------
    def intake_requirement(self, text: str) -> dict:
        """Treat requirement text as untrusted data; flag embedded injections."""
        suspicious = is_suspicious(text)
        if suspicious:
            # SECGOV-R3: redact secrets before the snippet enters the audit log.
            self.audit.append({"event": "injection_flagged", "snippet": redact(text[:200])})
        self.events.emit("requirement_intake", suspicious=suspicious, status="ok")
        return {"suspicious": suspicious, "sanitized": sanitize(text)}

    # --- BUILD-R5 + SECGOV-R5: guard a builder write -------------------------
    def guard_builder_write(self, path: str, source: str) -> None:
        """Block writes to framework files (raises FrameworkIntegrityError) and
        reject edits that break parseability (raises SyntaxGuardError)."""
        assert_writable(path)
        syntax_guard_write(path, source)

    # --- REVIEW-R4: security gate (secret scan + SAST) -----------------------
    def security_gate(self, source: str) -> dict:
        findings = scan_security(source)
        blocked = blocks_delivery(findings)
        self.events.emit(
            "security_gate",
            finding_count=len(findings),
            blocked=blocked,
            status="blocked" if blocked else "ok",
        )
        return {"ok": not blocked, "findings": findings}

    # --- REVIEW-R3: property-verification gate (EARS -> assertions) ----------
    def property_gate(self, spec_markdown: str, checks: dict):
        props = parse_scenarios(spec_markdown)
        report = verify_properties(props, checks)
        self.events.emit(
            "property_gate",
            passed=len(report.passed),
            failed=len(report.failed),
            ok=report.ok,
            status="ok" if report.ok else "blocked",
        )
        return report

    # --- BUILD-R4: per-worktree test resources (port + db) -------------------
    def allocate_test_resources(self, worktree: str) -> dict:
        if not hasattr(self, "_ports"):
            self._ports = PortAllocator()
        port = self._ports.allocate(worktree)
        db = self._ports.db_name(worktree)
        self.events.emit("test_resources", worktree=worktree, port=port, status="ok")
        return {"port": port, "db": db}

    # --- BUILD-R1/R3: parallel build under worktree isolation ----------------
    def build_with_worktrees(self, repo_root: str, batches: list, build_fn, into: str | None = None):
        """Run each batch in isolated git worktrees (one per owner-file), then
        3-way merge the branches back. build_fn(worktree_path, assignment).

        `into` is the branch to merge back into; when None it is detected from
        the repo's current HEAD (worktrees are forked from HEAD, so merging into
        a hard-coded 'main' breaks on repos whose default branch is 'master')."""
        if into is None:
            try:
                into = subprocess.run(
                    ["git", "-C", str(repo_root), "rev-parse", "--abbrev-ref", "HEAD"],
                    capture_output=True, text=True, check=True,
                ).stdout.strip() or "main"
            except Exception:
                into = "main"
        mgr = WorktreeManager(repo_root)
        reports = []
        try:
            for bi, batch in enumerate(batches):
                branches = []
                for assignment in batch:
                    owner = assignment.get("owner_file", f"b{bi}")
                    name = f"b{bi}-" + owner.replace("/", "_").replace(".", "_")
                    wt = mgr.create(name)
                    build_fn(wt, assignment)
                    branches.append(f"cw/{name}")
                reports.append(MergeCoordinator(repo_root).merge_branches(branches, into=into))
        finally:
            mgr.cleanup()
        return reports

    # --- Aider repo-map: ranked, budgeted code context -----------------------
    def context_map(self, files: dict, max_chars: int = 2000) -> str:
        return build_map(files, max_chars=max_chars)

    # --- SAFE-R1 / SECGOV-R4: run untrusted code in a sandbox ----------------
    def sandbox_for(self, root: str, deny_network: bool = True) -> Sandbox:
        """Return a sandbox confining untrusted/generated code to `root`."""
        self.events.emit("sandbox_created", root=root,
                         deny_network=deny_network, status="ok")
        return Sandbox(root, deny_network=deny_network)

    # --- ORCH-R1..R5: the deterministic fix loop -----------------------------
    def run_fix_loop(
        self,
        *,
        produce_fix: Callable[[int], dict],
        review: Callable[[dict, int], dict],
        max_iterations: int = 3,
        patience: int = 2,
        max_tokens: int | None = None,
        replan_fn: Callable[[], object] | None = None,
        phase: str = "fix",
    ) -> RunResult:
        """Run review-fix iterations under the three termination guards.

        produce_fix(iteration) -> {'diff': str, 'tokens': int}
        review(fix, iteration)  -> {'defects': iterable, 'tokens': int}

        Control flow (loops/termination) is decided here, not by the callables.
        """
        self.state.set_phase(phase)
        ctrl = TerminationController(
            max_iterations=max_iterations, patience=patience, max_tokens=max_tokens
        )
        iteration = 0
        defects: set = set()
        while True:
            fix = produce_fix(iteration) or {}
            result = review(fix, iteration) or {}
            defects = set(result.get("defects", []))
            tokens = int(fix.get("tokens", 0)) + int(result.get("tokens", 0))

            self.events.emit(
                "fix_iteration",
                iteration=iteration,
                defect_count=len(defects),
                tokens=tokens,
                status="ok",
            )
            self.audit.append(
                {"event": "fix_iteration", "iteration": iteration,
                 "defects": sorted(map(str, defects))}
            )
            self.state.mark_done(f"{phase}-{iteration}")
            self.state.checkpoint(self.state_path)

            if not defects:
                return RunResult(status="resolved", iterations=iteration + 1)

            reason = ctrl.step(iteration, defects, tokens=tokens)
            if reason:
                esc = self.escalator.handle(
                    reason,
                    diff=str(fix.get("diff", "")),
                    critique=", ".join(sorted(map(str, defects))),
                    replan_fn=replan_fn,
                )
                if esc is None:
                    # ORCH-R5: single replan resolved the blocker
                    self.events.emit("replan_resolved", reason=reason, status="ok")
                    return RunResult(
                        status="resolved_after_replan",
                        iterations=iteration + 1,
                        reason=reason,
                    )
                self.events.emit("escalated", reason=reason, status="escalated")
                self.audit.append({"event": "escalated", "reason": reason})
                return RunResult(
                    status="escalated",
                    iterations=iteration + 1,
                    reason=reason,
                    escalation=esc,
                    final_defects=sorted(map(str, defects)),
                )
            iteration += 1
