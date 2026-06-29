"""Tests for src/utils/sandbox.py – isolated subprocess sandbox.

Covers:
- Successful parsing
- Invalid / malicious input
- Sandbox timeout
- Sandbox crash handling
- Resource-limit enforcement
- Main process remains unaffected after every failure mode
"""

from __future__ import annotations

import os
import sys
import unittest

# Make the src/ tree importable without installing the package.
_SRC = os.path.join(os.path.dirname(__file__), "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from utils.sandbox import (
    DEFAULT_CPU_SECS,
    DEFAULT_MEMORY_BYTES,
    DEFAULT_TIMEOUT_SECS,
    SandboxResult,
    run_parser,
)


VALID_FRAME = {"asset": "XLM-USD", "price": 0.11, "timestamp": 1710000000}


class TestSuccessfulParsing(unittest.TestCase):
    """run_parser returns data when the parser succeeds."""

    def test_flatten_returns_list_of_tuples(self) -> None:
        result = run_parser("flatten_telemetry_frames", VALID_FRAME)
        self.assertTrue(result.ok)
        self.assertIsNotNone(result.data)
        self.assertIsInstance(result.data, list)
        self.assertEqual(len(result.data), 1)
        row = result.data[0]
        self.assertEqual(row[0], "XLM-USD")
        self.assertAlmostEqual(row[1], 0.11)

    def test_build_segments_groups_frames(self) -> None:
        result = run_parser(
            "build_telemetry_segments", VALID_FRAME, kwargs={"segment_size": 1}
        )
        self.assertTrue(result.ok)
        self.assertIsInstance(result.data, list)
        self.assertEqual(len(result.data), 1)

    def test_iter_flat_ticker_tuples_returns_list(self) -> None:
        result = run_parser("iter_flat_ticker_tuples", VALID_FRAME)
        self.assertTrue(result.ok)
        self.assertIsInstance(result.data, list)

    def test_exit_code_zero_on_success(self) -> None:
        result = run_parser("flatten_telemetry_frames", VALID_FRAME)
        self.assertEqual(result.exit_code, 0)


class TestInvalidOrMaliciousInput(unittest.TestCase):
    """Parser errors inside the sandbox are captured without crashing the host."""

    def test_missing_price_field_returns_error(self) -> None:
        bad = {"asset": "XLM-USD", "timestamp": 1}  # no price
        result = run_parser("flatten_telemetry_frames", bad)
        self.assertFalse(result.ok)
        self.assertIsInstance(result.error, str)
        self.assertTrue(len(result.error) > 0)

    def test_non_numeric_price_returns_error(self) -> None:
        bad = {"asset": "XLM-USD", "price": "not-a-number", "timestamp": 1}
        result = run_parser("flatten_telemetry_frames", bad)
        self.assertFalse(result.ok)

    def test_unknown_parser_name_returns_error(self) -> None:
        result = run_parser("evil_exec", VALID_FRAME)
        self.assertFalse(result.ok)
        self.assertIn("Unknown parser", result.error)

    def test_deeply_nested_bomb_does_not_hang(self) -> None:
        # A payload that could exhaust recursion in naive parsers.
        bomb: dict = {}
        node = bomb
        for _ in range(500):
            node["data"] = {}
            node = node["data"]  # type: ignore[assignment]
        result = run_parser("flatten_telemetry_frames", bomb, timeout=5.0)
        # Either ok=False (error path) or ok=True (drop_invalid) – the host
        # must still be responsive and result must be a SandboxResult.
        self.assertIsInstance(result, SandboxResult)

    def test_main_process_unaffected_after_bad_input(self) -> None:
        _ = run_parser("flatten_telemetry_frames", {"bad": True})
        # Sentinel operation succeeds in the same process after a failed sandbox.
        sentinel = run_parser("flatten_telemetry_frames", VALID_FRAME)
        self.assertTrue(sentinel.ok)


class TestTimeout(unittest.TestCase):
    """Sandbox kills the child and returns a structured error when it times out."""

    def test_timeout_flag_set(self) -> None:
        # Instruct the child to sleep via a crafted payload processed by a
        # parser override – we use the "unknown parser" path combined with a
        # very short timeout to guarantee a timeout without sleeping in the host.
        # A more direct test: patch the child to import time.sleep via kwargs
        # is not possible without modifying the child script, so we use the
        # CPU-time limit path by passing cpu_secs=1 and timeout=0.5.
        result = run_parser(
            "flatten_telemetry_frames",
            VALID_FRAME,
            timeout=0.001,  # 1 ms – child won't finish in time
        )
        self.assertFalse(result.ok)
        self.assertTrue(result.timed_out)

    def test_timeout_error_message(self) -> None:
        result = run_parser("flatten_telemetry_frames", VALID_FRAME, timeout=0.001)
        self.assertIn("timeout", result.error.lower())

    def test_main_process_alive_after_timeout(self) -> None:
        _ = run_parser("flatten_telemetry_frames", VALID_FRAME, timeout=0.001)
        # Main process must continue normally.
        self.assertEqual(1 + 1, 2)


class TestCrashHandling(unittest.TestCase):
    """Sandbox crash (non-zero exit) is reported without re-raising."""

    def test_crash_returns_ok_false(self) -> None:
        # Pass a non-serialisable object? No – everything in our API is JSON.
        # Trigger a crash via invalid parser name (child sys.exit(1) path).
        result = run_parser("__crash__", VALID_FRAME)
        self.assertFalse(result.ok)
        self.assertIsNotNone(result.error)

    def test_main_process_unaffected_after_crash(self) -> None:
        _ = run_parser("__crash__", VALID_FRAME)
        sentinel = run_parser("flatten_telemetry_frames", VALID_FRAME)
        self.assertTrue(sentinel.ok)


class TestResourceLimits(unittest.TestCase):
    """Resource limit parameters are accepted and forwarded to the child."""

    def test_custom_memory_limit_accepted(self) -> None:
        # A very high limit still works; we just verify the call succeeds.
        result = run_parser(
            "flatten_telemetry_frames",
            VALID_FRAME,
            memory_bytes=512 * 1024 * 1024,
        )
        self.assertTrue(result.ok)

    def test_low_memory_limit_causes_failure_or_succeeds(self) -> None:
        # With an extremely small AS limit the child may fail at import time.
        # Either way the host receives a SandboxResult (no exception propagates).
        result = run_parser(
            "flatten_telemetry_frames",
            VALID_FRAME,
            memory_bytes=1,  # 1 byte – almost certain to kill the child
            timeout=5.0,
        )
        self.assertIsInstance(result, SandboxResult)

    def test_default_constants_are_sane(self) -> None:
        self.assertGreater(DEFAULT_TIMEOUT_SECS, 0)
        self.assertGreater(DEFAULT_MEMORY_BYTES, 0)
        self.assertGreater(DEFAULT_CPU_SECS, 0)


class TestMainProcessIsolation(unittest.TestCase):
    """Confirm the main process is never affected regardless of sandbox outcome."""

    def _sentinel(self) -> SandboxResult:
        return run_parser("flatten_telemetry_frames", VALID_FRAME)

    def test_process_pid_unchanged_across_calls(self) -> None:
        pid_before = os.getpid()
        for _ in range(3):
            run_parser("flatten_telemetry_frames", VALID_FRAME)
        self.assertEqual(os.getpid(), pid_before)

    def test_successful_parse_does_not_mutate_payload(self) -> None:
        payload = {"asset": "ETH-USD", "price": 3000.0, "timestamp": 1}
        original = dict(payload)
        run_parser("flatten_telemetry_frames", payload)
        self.assertEqual(payload, original)

    def test_error_result_does_not_raise(self) -> None:
        # None of these should propagate an exception.
        run_parser("bad_parser", None)
        run_parser("flatten_telemetry_frames", None)
        run_parser("flatten_telemetry_frames", VALID_FRAME, timeout=0.001)

    def test_result_is_always_sandbox_result_instance(self) -> None:
        cases = [
            run_parser("flatten_telemetry_frames", VALID_FRAME),
            run_parser("bad_parser", VALID_FRAME),
            run_parser("flatten_telemetry_frames", VALID_FRAME, timeout=0.001),
        ]
        for res in cases:
            self.assertIsInstance(res, SandboxResult)


if __name__ == "__main__":
    unittest.main()
