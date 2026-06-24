"""
portman.py — Port allocation and health-check utilities for CodexAutoAI v2.

BUILD-R4: Replaces the old `sleep 3` + everything-bound-to-:8000 approach with:
  - Per-worktree unique port assignment via OS-assigned ephemeral ports
  - Health-check polling (no fixed long sleeps) to detect server readiness
  - Unique DB name derivation per worktree key
"""

import hashlib
import socket
import time
import threading
from typing import Callable, Tuple, Union


def find_free_port() -> int:
    """Bind to port 0 to let the OS assign a free ephemeral port, then return it.

    The port is released before returning.  Callers should bind their server
    to this port promptly to minimise the chance of a race.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class PortAllocator:
    """Assigns a stable, unique free port (and DB name) per worktree key.

    Args:
        base: Lowest port number the allocator will try first.  The allocator
              may assign ports above this value as needed.
    """

    def __init__(self, base: int = 8001) -> None:
        self._base = base
        self._lock = threading.Lock()
        # Maps worktree key -> assigned port
        self._port_map: dict[str, int] = {}
        # Tracks ports already handed out (set of ints)
        self._used_ports: set[int] = set()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def allocate(self, worktree: str) -> int:
        """Return a unique free port for *worktree*.

        Idempotent: calling with the same key twice returns the same port.
        Different keys always receive different ports.
        """
        with self._lock:
            if worktree in self._port_map:
                return self._port_map[worktree]
            port = self._pick_free_port()
            self._port_map[worktree] = port
            self._used_ports.add(port)
            return port

    def db_name(self, worktree: str, prefix: str = "app") -> str:
        """Return a unique DB name for *worktree*.

        Format: ``<prefix>_<8-hex-chars>``.
        The hex suffix is derived from the worktree key so the name is
        deterministic and repeatable for the same key.
        """
        digest = hashlib.sha256(worktree.encode()).hexdigest()[:8]
        return f"{prefix}_{digest}"

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _pick_free_port(self) -> int:
        """Find a free OS port that hasn't already been handed out."""
        # Ask the OS for a free port.  Retry until we get one we haven't
        # allocated yet (extremely unlikely to need more than one try).
        for _ in range(64):
            port = find_free_port()
            if port not in self._used_ports:
                return port
        # Absolute fallback — should never be reached in practice
        raise RuntimeError("PortAllocator: could not find a unique free port after 64 attempts")


# ---------------------------------------------------------------------------
# Health-check polling
# ---------------------------------------------------------------------------

def wait_for_health(
    url_or_check: Union[Callable[[], bool], Tuple[str, int]],
    timeout: float = 10.0,
    interval: float = 0.1,
) -> bool:
    """Poll until healthy or *timeout* seconds elapse.

    Args:
        url_or_check: Either

            * a zero-argument callable that returns ``True`` when healthy, or
            * a ``(host, port)`` tuple — the function will attempt a TCP
              connection and treat a successful connect as healthy.

        timeout: Maximum seconds to wait.  Returns ``False`` if not healthy
                 within this window.  A value of 0 means "check once immediately".
        interval: Seconds to sleep between attempts (no-op sleep avoided when
                  the check succeeds on the first attempt).

    Returns:
        ``True`` if the health check passes before *timeout* elapses,
        ``False`` otherwise.  **Never sleeps longer than needed** — returns
        immediately on success.
    """
    if callable(url_or_check):
        checker = url_or_check
    else:
        host, port = url_or_check
        def checker() -> bool:  # type: ignore[misc]
            try:
                with socket.create_connection((host, port), timeout=min(interval, 1.0)):
                    return True
            except OSError:
                return False

    deadline = time.monotonic() + timeout

    while True:
        if checker():
            return True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        # Sleep only as long as needed (but not past the deadline)
        time.sleep(min(interval, remaining))
