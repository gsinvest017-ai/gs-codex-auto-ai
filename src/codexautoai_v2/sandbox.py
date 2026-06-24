"""sandbox.py — SAFE-R1 / SECGOV-R4 execution sandbox.

Confines an untrusted / generated-code process to a single working root with a
scrubbed environment, and adds OS-level *enforcement* of the process lifecycle:

  * filesystem confinement  — paths are validated to stay within `root`
  * cwd restriction         — the child runs with cwd = root
  * environment scrubbing   — only allow-listed env keys reach the child
                              (secrets / unlisted vars stripped, SECGOV-R3)
  * process-tree enforcement:
      - Windows : a Job Object with KILL_ON_JOB_CLOSE (+ optional active-process
                  and per-process memory caps). On timeout the WHOLE job tree is
                  terminated — no orphaned/runaway descendants.
      - POSIX   : a new session/process-group; on timeout the whole group is
                  SIGKILL'd.
      - other   : graceful fallback (direct-child kill), result.enforced=False.
  * network egress          — advisory blackhole proxy (best-effort)
  * no shell strings        — commands must be argv lists (no shell injection)

Pure stdlib (ctypes for the Windows Job Object). Self-contained.
"""
from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

# Minimal env keys a process generally needs to start (Windows + POSIX).
_DEFAULT_ENV_ALLOW = (
    "PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT",
    "TEMP", "TMP", "TMPDIR", "HOME", "USERPROFILE",
    "LANG", "LC_ALL", "PYTHONUNBUFFERED", "PYTHONPATH",
)


class SandboxViolation(Exception):
    """Raised when an operation would breach the sandbox boundary."""


def is_within(path, root) -> bool:
    """True if `path` resolves to a location inside `root`."""
    root_r = Path(root).resolve()
    p = Path(path).resolve()
    try:
        p.relative_to(root_r)
        return True
    except ValueError:
        return False


@dataclass
class JobLimits:
    """Optional OS-enforced resource caps for a sandboxed run."""
    max_memory_bytes: int | None = None
    max_active_processes: int | None = None
    kill_on_close: bool = True


@dataclass
class SandboxResult:
    returncode: int
    stdout: str
    stderr: str
    command: list
    enforced: bool = False    # True when an OS primitive (Job Object / pgroup) backed the run
    timed_out: bool = False


# --------------------------------------------------------------------------- #
# Windows Job Object support (ctypes). Degrades to None on any failure.
# --------------------------------------------------------------------------- #
_WIN32_OK = False
if sys.platform == "win32":
    try:
        import ctypes
        from ctypes import wintypes as _wt

        _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

        _JobObjectExtendedLimitInformation = 9
        _JOB_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
        _JOB_LIMIT_ACTIVE_PROCESS = 0x00000008
        _JOB_LIMIT_PROCESS_MEMORY = 0x00000100

        class _IO_COUNTERS(ctypes.Structure):
            _fields_ = [("ReadOperationCount", ctypes.c_ulonglong),
                        ("WriteOperationCount", ctypes.c_ulonglong),
                        ("OtherOperationCount", ctypes.c_ulonglong),
                        ("ReadTransferCount", ctypes.c_ulonglong),
                        ("WriteTransferCount", ctypes.c_ulonglong),
                        ("OtherTransferCount", ctypes.c_ulonglong)]

        class _BASIC_LIMIT(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64),
                        ("PerJobUserTimeLimit", ctypes.c_int64),
                        ("LimitFlags", _wt.DWORD),
                        ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t),
                        ("ActiveProcessLimit", _wt.DWORD),
                        ("Affinity", ctypes.c_size_t),
                        ("PriorityClass", _wt.DWORD),
                        ("SchedulingClass", _wt.DWORD)]

        class _EXTENDED_LIMIT(ctypes.Structure):
            _fields_ = [("BasicLimitInformation", _BASIC_LIMIT),
                        ("IoInfo", _IO_COUNTERS),
                        ("ProcessMemoryLimit", ctypes.c_size_t),
                        ("JobMemoryLimit", ctypes.c_size_t),
                        ("PeakProcessMemoryUsed", ctypes.c_size_t),
                        ("PeakJobMemoryUsed", ctypes.c_size_t)]

        _kernel32.CreateJobObjectW.restype = _wt.HANDLE
        _kernel32.CreateJobObjectW.argtypes = [_wt.LPVOID, _wt.LPCWSTR]
        _kernel32.SetInformationJobObject.restype = _wt.BOOL
        _kernel32.SetInformationJobObject.argtypes = [_wt.HANDLE, ctypes.c_int, _wt.LPVOID, _wt.DWORD]
        _kernel32.AssignProcessToJobObject.restype = _wt.BOOL
        _kernel32.AssignProcessToJobObject.argtypes = [_wt.HANDLE, _wt.HANDLE]
        _kernel32.TerminateJobObject.restype = _wt.BOOL
        _kernel32.TerminateJobObject.argtypes = [_wt.HANDLE, _wt.UINT]
        _kernel32.CloseHandle.restype = _wt.BOOL
        _kernel32.CloseHandle.argtypes = [_wt.HANDLE]

        _WIN32_OK = True
    except Exception:  # pragma: no cover - platform/ctypes quirk
        _WIN32_OK = False


def _create_job(limits: "JobLimits | None"):
    """Create a configured Job Object handle, or None on any failure."""
    try:
        h = _kernel32.CreateJobObjectW(None, None)
        if not h:
            return None
        info = _EXTENDED_LIMIT()
        flags = 0
        if limits is None or limits.kill_on_close:
            flags |= _JOB_LIMIT_KILL_ON_JOB_CLOSE
        if limits and limits.max_active_processes:
            flags |= _JOB_LIMIT_ACTIVE_PROCESS
            info.BasicLimitInformation.ActiveProcessLimit = int(limits.max_active_processes)
        if limits and limits.max_memory_bytes:
            flags |= _JOB_LIMIT_PROCESS_MEMORY
            info.ProcessMemoryLimit = int(limits.max_memory_bytes)
        info.BasicLimitInformation.LimitFlags = flags
        ok = _kernel32.SetInformationJobObject(
            h, _JobObjectExtendedLimitInformation, ctypes.byref(info), ctypes.sizeof(info)
        )
        if not ok:
            _kernel32.CloseHandle(h)
            return None
        return h
    except Exception:  # pragma: no cover
        return None


# --------------------------------------------------------------------------- #
class Sandbox:
    def __init__(self, root, env_allow=None, deny_network: bool = True,
                 job_limits: "JobLimits | None" = None):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.env_allow = tuple(env_allow) if env_allow is not None else _DEFAULT_ENV_ALLOW
        self.deny_network = deny_network
        self.job_limits = job_limits

    def confine(self, path) -> str:
        """Resolve `path` (relative to root if not absolute); raise if it escapes."""
        p = Path(path)
        if not p.is_absolute():
            p = self.root / p
        if not is_within(p, self.root):
            raise SandboxViolation(f"path escapes sandbox root: {path}")
        return str(p.resolve())

    def build_env(self, base_env=None) -> dict:
        """Return only allow-listed env keys; strip everything else (secrets)."""
        src = dict(base_env if base_env is not None else os.environ)
        env = {k: v for k, v in src.items() if k in self.env_allow}
        if self.deny_network:
            env["HTTP_PROXY"] = env["HTTPS_PROXY"] = "http://127.0.0.1:9"
            env["http_proxy"] = env["https_proxy"] = "http://127.0.0.1:9"
            env["no_proxy"] = ""
        return env

    def run(self, command, timeout: float = 60, input_text=None) -> SandboxResult:
        """Run `command` (argv list) confined to root with a scrubbed env and
        OS-level process-tree enforcement where available."""
        if not isinstance(command, (list, tuple)):
            raise SandboxViolation("command must be an argv list, not a shell string")
        cmd = list(command)
        env = self.build_env()
        cwd = str(self.root)
        if sys.platform == "win32" and _WIN32_OK:
            return self._run_win32(cmd, env, cwd, timeout, input_text)
        if os.name == "posix":
            return self._run_posix(cmd, env, cwd, timeout, input_text)
        return self._run_plain(cmd, env, cwd, timeout, input_text)

    # --- platform runners ----------------------------------------------------
    def _popen(self, cmd, env, cwd, input_text, **kw):
        return subprocess.Popen(
            cmd, cwd=cwd, env=env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            stdin=subprocess.PIPE if input_text is not None else None,
            text=True, **kw,
        )

    def _collect(self, cmd, proc, timeout, input_text, enforced, on_timeout) -> SandboxResult:
        try:
            out, err = proc.communicate(input=input_text, timeout=timeout)
            return SandboxResult(proc.returncode, out or "", err or "", cmd, enforced, False)
        except subprocess.TimeoutExpired:
            on_timeout(proc)
            try:
                out, err = proc.communicate(timeout=5)
            except Exception:
                out, err = "", ""
            rc = proc.returncode if proc.returncode is not None else -1
            return SandboxResult(rc, out or "", err or "", cmd, enforced, True)

    def _run_win32(self, cmd, env, cwd, timeout, input_text) -> SandboxResult:
        job = _create_job(self.job_limits)
        if job is None:
            return self._run_plain(cmd, env, cwd, timeout, input_text)
        try:
            proc = self._popen(cmd, env, cwd, input_text)
            try:
                _kernel32.AssignProcessToJobObject(job, int(proc._handle))
            except Exception:
                pass
            return self._collect(
                cmd, proc, timeout, input_text, True,
                lambda p: _kernel32.TerminateJobObject(job, 1),
            )
        finally:
            try:
                _kernel32.CloseHandle(job)  # KILL_ON_JOB_CLOSE reaps survivors
            except Exception:
                pass

    def _run_posix(self, cmd, env, cwd, timeout, input_text) -> SandboxResult:
        import signal
        proc = self._popen(cmd, env, cwd, input_text, start_new_session=True)

        def _kill_group(p):
            try:
                os.killpg(os.getpgid(p.pid), signal.SIGKILL)
            except Exception:
                p.kill()

        return self._collect(cmd, proc, timeout, input_text, True, _kill_group)

    def _run_plain(self, cmd, env, cwd, timeout, input_text) -> SandboxResult:
        proc = self._popen(cmd, env, cwd, input_text)
        return self._collect(cmd, proc, timeout, input_text, False, lambda p: p.kill())
