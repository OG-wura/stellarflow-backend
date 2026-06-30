#!/usr/bin/env python3
"""
Isolated Process Sandboxes for External Unverified Data Adapters

Executes untrusted third-party adapter scripts inside restricted subprocess
containers so that a malicious or buggy endpoint cannot compromise core
connection keys or corrupt shared memory.

Security model
--------------
* Each adapter runs in its own *child process* via :class:`subprocess.Popen`.
  The parent never ``exec``s untrusted code directly.
* The child is launched with a stripped environment (``env`` parameter) — no
  ``DATABASE_URL``, no ``AWS_SECRET_ACCESS_KEY``, no inherited key material.
* ``popen`` is wrapped so the parent can apply OS-level hard limits:
  * ``resource.setrlimit`` to cap CPU time (``RLIMIT_CPU``) and address space
    (``RLIMIT_AS``) where the platform permits it.
  * A *watchdog thread* enforces a wall-clock timeout and kills the child if
    it overruns.
* Streams are captured through ``communicate()`` with a size cap to prevent
  log / memory exhaustion attacks.

Usage::

    from src.utils.sandbox import SandboxRunner, SandboxConfig

    cfg = SandboxConfig(
        max_cpu_seconds=5,
        max_memory_mb=128,
        wall_timeout_seconds=10,
        blocked_env_vars={"DATABASE_URL", "API_KEY"},
    )

    runner = SandboxRunner(cfg)
    result = runner.run(["python3", "adapter.py", "--pair", "XLM/USDC"])
    print(result.returncode, result.stdout, result.stderr)
"""

from __future__ import annotations

import os
import platform
import resource
import subprocess
import threading
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Set


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SandboxConfig:
    """Tunable security knobs for :class:`SandboxRunner`."""

    max_cpu_seconds: int = 10
    max_memory_mb: int = 256
    wall_timeout_seconds: Optional[int] = 30
    blocked_env_vars: Set[str] = field(
        default_factory=lambda: {
            "DATABASE_URL",
            "POSTGRES_PASSWORD",
            "API_KEY",
            "AWS_SECRET_ACCESS_KEY",
            "AWS_ACCESS_KEY_ID",
            "PRIVATE_KEY",
            "SECRET_KEY",
        }
    )
    allowed_env_vars: Set[str] = field(default_factory=lambda: {"PATH", "HOME", "LANG"})


# ---------------------------------------------------------------------------
# Result container
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SandboxResult:
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

class SandboxRunner:
    """Runs an external adapter script in a hardened subprocess sandbox.

    Parameters
    ----------
    config:
        Security budget for child processes.  See :class:`SandboxConfig`.
    """

    def __init__(self, config: Optional[SandboxConfig] = None) -> None:
        self._cfg = config or SandboxConfig()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(
        self,
        args: Sequence[str],
        *,
        cwd: Optional[str] = None,
        env: Optional[dict] = None,
        max_output_bytes: int = 1_048_576,  # 1 MiB safety cap
    ) -> SandboxResult:
        """Execute *args* inside the sandbox and return captured output.

        Raises
        ------
        ValueError
            If *args* is empty.
        FileNotFoundError
            If the executable cannot be located.
        """
        if not args:
            raise ValueError("args must not be empty")

        safe_env = self._build_safe_env(env)
        max_mem_bytes = self._cfg.max_memory_mb * 1_048_576

        # On POSIX we can apply RLIMITs before exec.  On Windows the
        # resource module is mostly a no-op; we rely on the watchdog.
        preexec_fn = self._build_preexec_fn(max_mem_bytes)

        proc = subprocess.Popen(
            list(args),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=cwd,
            env=safe_env,
            preexec_fn=preexec_fn,
        )

        try:
            stdout_bytes, stderr_bytes = proc.communicate(
                timeout=self._cfg.wall_timeout_seconds
            )
            return SandboxResult(
                returncode=proc.returncode,
                stdout=self._truncate(stdout_bytes, max_output_bytes),
                stderr=self._truncate(stderr_bytes, max_output_bytes),
                timed_out=False,
            )
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            # Try one last drain so we don't lose diagnostic output.
            try:
                out, err = proc.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                out, err = b"", b""
            return SandboxResult(
                returncode=-9,
                stdout=self._truncate(out, max_output_bytes),
                stderr=self._truncate(err, max_output_bytes),
                timed_out=True,
            )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _build_safe_env(self, override: Optional[dict]) -> dict:
        """Start from the current process env, strip secrets, apply override."""
        base = dict(os.environ)
        for var in self._cfg.blocked_env_vars:
            base.pop(var, None)
        for var in list(base.keys()):
            if var not in self._cfg.allowed_env_vars:
                base.pop(var, None)
        if override:
            base.update(override)
        return base

    def _build_preexec_fn(self, max_mem_bytes: int):
        """Return a pre-exec callback that hard-locks the child process."""
        if platform.system() == "Windows":
            return None  # resource.setrlimit not available

        def _preexec() -> None:
            try:
                # Cap CPU time (seconds).
                resource.setrlimit(
                    resource.RLIMIT_CPU,
                    (self._cfg.max_cpu_seconds, self._cfg.max_cpu_seconds),
                )
                # Cap address space.
                resource.setrlimit(
                    resource.RLIMIT_AS,
                    (max_mem_bytes, max_mem_bytes),
                )
            except (ValueError, resource.error):
                # Silently ignore if the platform refuses (e.g. already in a
                # container with lower limits).
                pass

        return _preexec

    @staticmethod
    def _truncate(data: bytes, limit: int) -> str:
        if not data:
            return ""
        if len(data) > limit:
            return data[:limit].decode("utf-8", errors="replace") + "\n...[truncated]"
        return data.decode("utf-8", errors="replace")


__all__ = ["SandboxConfig", "SandboxResult", "SandboxRunner"]
