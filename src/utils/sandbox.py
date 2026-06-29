"""Isolated subprocess sandbox for untrusted feed parsers.

Untrusted external feed payloads are parsed inside a short-lived child process
so that parser crashes, infinite loops, or malicious inputs cannot affect the
main process.  Communication uses JSON over stdin/stdout; resource limits are
enforced with the POSIX ``resource`` module before the child executes.

Architecture
------------
* The **host** calls :func:`run_parser` with a parser name and raw payload.
* A child ``python3`` process is spawned; it imports and calls the requested
  parser function from ``ingestion.parser`` and writes the result as JSON to
  stdout before exiting.
* The host reads the child's JSON response and returns a :class:`SandboxResult`.
* If the child times out, exceeds memory, or crashes, a structured error is
  returned without raising – the main process remains unaffected.

Resource limits (Linux / macOS via ``resource`` module)
--------------------------------------------------------
* ``RLIMIT_AS``  – virtual-address-space cap (default 256 MiB).
* ``RLIMIT_CPU`` – CPU-time cap in seconds (default 5 s).
* Execution wall-clock timeout enforced with ``subprocess.communicate(timeout=)``.
"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# Public constants
# ---------------------------------------------------------------------------

DEFAULT_TIMEOUT_SECS: float = 10.0
DEFAULT_MEMORY_BYTES: int = 256 * 1024 * 1024  # 256 MiB
DEFAULT_CPU_SECS: int = 5

# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass
class SandboxResult:
    """Outcome of a sandboxed parser invocation."""

    ok: bool
    data: Any = None
    error: str = ""
    exit_code: int | None = None
    timed_out: bool = False
    resource_error: bool = False


# ---------------------------------------------------------------------------
# Child-process bootstrap (runs inside the sandbox)
# ---------------------------------------------------------------------------

_CHILD_SCRIPT = textwrap.dedent("""\
    import json, sys, resource

    def _apply_limits(memory_bytes, cpu_secs):
        try:
            resource.setrlimit(resource.RLIMIT_AS, (memory_bytes, memory_bytes))
        except (ValueError, resource.error):
            pass
        try:
            resource.setrlimit(resource.RLIMIT_CPU, (cpu_secs, cpu_secs))
        except (ValueError, resource.error):
            pass

    def main():
        req = json.loads(sys.stdin.read())
        _apply_limits(req["memory_bytes"], req["cpu_secs"])

        parser_name = req["parser"]
        payload = req["payload"]
        kwargs = req.get("kwargs", {})

        # Dynamic import keeps the child isolated from any state in the host.
        if parser_name == "flatten_telemetry_frames":
            from ingestion.parser import flatten_telemetry_frames
            result = flatten_telemetry_frames([payload], **kwargs)
        elif parser_name == "build_telemetry_segments":
            from ingestion.parser import build_telemetry_segments
            result = build_telemetry_segments([payload], **kwargs)
        elif parser_name == "iter_flat_ticker_tuples":
            from ingestion.parser import iter_flat_ticker_tuples
            result = list(iter_flat_ticker_tuples([payload], **kwargs))
        else:
            raise ValueError(f"Unknown parser: {parser_name!r}")

        # Tuples are not JSON-serialisable; convert to lists.
        def _to_json(obj):
            if isinstance(obj, tuple):
                return [_to_json(v) for v in obj]
            if isinstance(obj, list):
                return [_to_json(v) for v in obj]
            return obj

        sys.stdout.write(json.dumps({"ok": True, "data": _to_json(result)}))

    try:
        main()
    except Exception as exc:
        sys.stdout.write(json.dumps({"ok": False, "error": str(exc)}))
        sys.exit(1)
""")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def run_parser(
    parser_name: str,
    payload: Any,
    *,
    timeout: float = DEFAULT_TIMEOUT_SECS,
    memory_bytes: int = DEFAULT_MEMORY_BYTES,
    cpu_secs: int = DEFAULT_CPU_SECS,
    kwargs: dict[str, Any] | None = None,
    pythonpath: str | None = None,
) -> SandboxResult:
    """Run *parser_name* on *payload* inside an isolated subprocess.

    Parameters
    ----------
    parser_name:
        One of ``"flatten_telemetry_frames"``, ``"build_telemetry_segments"``,
        or ``"iter_flat_ticker_tuples"``.
    payload:
        JSON-serialisable feed payload passed to the parser.
    timeout:
        Wall-clock seconds before the child is killed.
    memory_bytes:
        Virtual-address-space limit for the child process.
    cpu_secs:
        CPU-time limit (seconds) for the child process.
    kwargs:
        Extra keyword arguments forwarded to the parser function.
    pythonpath:
        Value for ``PYTHONPATH`` in the child environment.  Defaults to
        ``src`` relative to the project root so ``ingestion.parser`` is
        importable.

    Returns
    -------
    SandboxResult
        Always returns; never raises.
    """
    import os

    request = json.dumps(
        {
            "parser": parser_name,
            "payload": payload,
            "kwargs": kwargs or {},
            "memory_bytes": memory_bytes,
            "cpu_secs": cpu_secs,
        }
    )

    env = os.environ.copy()
    if pythonpath is not None:
        env["PYTHONPATH"] = pythonpath
    elif "PYTHONPATH" not in env:
        # Default: make src/ importable (mirrors the test invocation).
        project_root = os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        )
        env["PYTHONPATH"] = os.path.join(project_root, "src")

    try:
        proc = subprocess.Popen(
            [sys.executable, "-c", _CHILD_SCRIPT],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
        )

        try:
            stdout, _ = proc.communicate(
                input=request.encode(), timeout=timeout
            )
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()
            return SandboxResult(ok=False, error="sandbox timeout", timed_out=True)

        exit_code = proc.returncode

        if not stdout:
            return SandboxResult(
                ok=False,
                error="sandbox produced no output",
                exit_code=exit_code,
            )

        response = json.loads(stdout.decode())
        if response.get("ok"):
            return SandboxResult(ok=True, data=response["data"], exit_code=exit_code)
        else:
            return SandboxResult(
                ok=False,
                error=response.get("error", "unknown parser error"),
                exit_code=exit_code,
            )

    except json.JSONDecodeError as exc:
        return SandboxResult(ok=False, error=f"invalid sandbox response: {exc}")
    except OSError as exc:
        return SandboxResult(ok=False, error=f"failed to launch sandbox: {exc}")


__all__ = [
    "DEFAULT_CPU_SECS",
    "DEFAULT_MEMORY_BYTES",
    "DEFAULT_TIMEOUT_SECS",
    "SandboxResult",
    "run_parser",
]
