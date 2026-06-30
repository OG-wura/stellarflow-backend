"""Tests for src/ingestion/parser.py.

Covers:
  - All original flattening and segmenting behaviour (regression).
  - The new SIMD-accelerated ``parse_raw_pack()`` entry point:
      * Single-object raw pack
      * Top-level array raw pack
      * Nested batch inside raw pack
      * drop_invalid=True filters malformed frames
      * Empty bytes raises ValueError
  - ``SIMDJSON_AVAILABLE`` is a bool (path introspection).
  - Fallback path: monkeypatching ``_simd_decode`` to ``json.loads`` still
    produces correct output so we have coverage independent of whether the
    native extension is installed.
  - ``parsers`` compatibility shim re-exports all expected names.
"""
from __future__ import annotations

import json
import unittest
import unittest.mock

import ingestion.parser as _parser_mod
from ingestion.parser import (
    SIMDJSON_AVAILABLE,
    build_telemetry_segments,
    flatten_telemetry_frames,
    parse_raw_pack,
)


# ---------------------------------------------------------------------------
# Regression tests — original API must remain green
# ---------------------------------------------------------------------------


class TestFlattenTelemetryFrames(unittest.TestCase):
    def test_collapses_nested_websocket_batches(self) -> None:
        payloads = [
            {
                "data": {
                    "tickers": [
                        {
                            "symbol": "xlm-usd",
                            "last_price": "0.1025",
                            "event_time": "1710000000123",
                            "seq": "101",
                        },
                        {
                            "ticker": {
                                "asset_id": "btc-usd",
                                "price": 64000.25,
                                "timestamp": 1710000000456,
                                "flags": 3,
                            }
                        },
                    ]
                }
            },
            {
                "frames": [
                    {
                        "pair": "eth-usd",
                        "value": "3150.75",
                        "ts": 1710000000999,
                        "nonce": 7,
                        "flag_bits": "2",
                    }
                ]
            },
        ]

        self.assertEqual(
            flatten_telemetry_frames(payloads),
            (
                ("XLM-USD", 0.1025, 1710000000123, 101, 0),
                ("BTC-USD", 64000.25, 1710000000456, 0, 3),
                ("ETH-USD", 3150.75, 1710000000999, 7, 2),
            ),
        )

    def test_can_skip_invalid_payloads(self) -> None:
        payloads = [
            {"asset": "xlm-usd", "price": 0.11, "timestamp": 1},
            {"asset": "bad-frame", "timestamp": 2},
            {"payload": {"ticker": {"asset": "eth-usd", "price": "3.50", "time": "3"}}},
            "ignored",
        ]

        self.assertEqual(
            flatten_telemetry_frames(payloads, drop_invalid=True),
            (
                ("XLM-USD", 0.11, 1, 0, 0),
                ("ETH-USD", 3.5, 3, 0, 0),
            ),
        )


class TestBuildTelemetrySegments(unittest.TestCase):
    def test_groups_frames_into_fixed_size_batches(self) -> None:
        payloads = [
            [
                {"asset": "xlm-usd", "price": 0.11, "timestamp": 1},
                {"asset": "btc-usd", "price": 1.22, "timestamp": 2},
                {"asset": "eth-usd", "price": 2.33, "timestamp": 3},
            ]
        ]

        self.assertEqual(
            build_telemetry_segments(payloads, segment_size=2),
            (
                (("XLM-USD", 0.11, 1, 0, 0), ("BTC-USD", 1.22, 2, 0, 0)),
                (("ETH-USD", 2.33, 3, 0, 0),),
            ),
        )

    def test_rejects_non_positive_segment_size(self) -> None:
        with self.assertRaisesRegex(ValueError, "segment_size"):
            _ = build_telemetry_segments([], segment_size=0)


# ---------------------------------------------------------------------------
# New tests — parse_raw_pack (SIMD ingestion entry point)
# ---------------------------------------------------------------------------


class TestParseRawPack(unittest.TestCase):
    """Tests for the new SIMD-accelerated parse_raw_pack() function."""

    # ------------------------------------------------------------------
    # Happy-path: single object
    # ------------------------------------------------------------------

    def test_single_frame_object(self) -> None:
        raw = json.dumps(
            {"asset": "xlm-usd", "price": 0.108, "timestamp": 1710000000001}
        ).encode()

        self.assertEqual(
            parse_raw_pack(raw),
            (("XLM-USD", 0.108, 1710000000001, 0, 0),),
        )

    def test_single_frame_object_with_optional_fields(self) -> None:
        raw = json.dumps(
            {
                "symbol": "btc-usd",
                "last_price": "64000.50",
                "event_time": "1710000000999",
                "seq": "42",
                "flags": "1",
            }
        ).encode()

        self.assertEqual(
            parse_raw_pack(raw),
            (("BTC-USD", 64000.5, 1710000000999, 42, 1),),
        )

    # ------------------------------------------------------------------
    # Happy-path: top-level array
    # ------------------------------------------------------------------

    def test_top_level_array_of_frames(self) -> None:
        frames = [
            {"asset": "xlm-usd", "price": 0.11, "timestamp": 1},
            {"asset": "btc-usd", "price": 64000.0, "timestamp": 2},
            {"asset": "eth-usd", "price": 3200.0, "timestamp": 3},
        ]
        raw = json.dumps(frames).encode()

        self.assertEqual(
            parse_raw_pack(raw),
            (
                ("XLM-USD", 0.11, 1, 0, 0),
                ("BTC-USD", 64000.0, 2, 0, 0),
                ("ETH-USD", 3200.0, 3, 0, 0),
            ),
        )

    # ------------------------------------------------------------------
    # Happy-path: nested/wrapped batch shapes
    # ------------------------------------------------------------------

    def test_nested_data_tickers_batch(self) -> None:
        pack = {
            "data": {
                "tickers": [
                    {"symbol": "xlm-usd", "last_price": "0.1025", "event_time": "1710000000123"},
                    {"symbol": "btc-usd", "last_price": "64000.25", "event_time": "1710000000456"},
                ]
            }
        }
        raw = json.dumps(pack).encode()

        result = parse_raw_pack(raw)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0][0], "XLM-USD")
        self.assertEqual(result[1][0], "BTC-USD")

    def test_frames_batch_key(self) -> None:
        pack = {
            "frames": [
                {"pair": "eth-usd", "value": "3150.75", "ts": 1710000000999, "nonce": 7}
            ]
        }
        raw = json.dumps(pack).encode()

        self.assertEqual(
            parse_raw_pack(raw),
            (("ETH-USD", 3150.75, 1710000000999, 7, 0),),
        )

    # ------------------------------------------------------------------
    # drop_invalid behaviour
    # ------------------------------------------------------------------

    def test_drop_invalid_skips_malformed_frames(self) -> None:
        frames = [
            {"asset": "xlm-usd", "price": 0.11, "timestamp": 1},    # valid
            {"asset": "bad-frame", "timestamp": 2},                   # missing price
            {"asset": "eth-usd", "price": 3200.0, "timestamp": 3},   # valid
        ]
        raw = json.dumps(frames).encode()

        result = parse_raw_pack(raw, drop_invalid=True)
        self.assertEqual(result, (("XLM-USD", 0.11, 1, 0, 0), ("ETH-USD", 3200.0, 3, 0, 0)))

    def test_drop_invalid_false_raises_on_malformed(self) -> None:
        frames = [
            {"asset": "xlm-usd", "price": 0.11, "timestamp": 1},
            {"asset": "bad-frame", "timestamp": 2},   # missing price — should raise
        ]
        raw = json.dumps(frames).encode()

        with self.assertRaises((ValueError, TypeError)):
            parse_raw_pack(raw, drop_invalid=False)

    # ------------------------------------------------------------------
    # Edge / error cases
    # ------------------------------------------------------------------

    def test_empty_bytes_raises_value_error(self) -> None:
        with self.assertRaisesRegex(ValueError, "empty"):
            parse_raw_pack(b"")

    def test_returns_telemetry_segment_type(self) -> None:
        raw = json.dumps({"asset": "xlm-usd", "price": 0.1, "timestamp": 100}).encode()
        result = parse_raw_pack(raw)
        self.assertIsInstance(result, tuple)
        for item in result:
            self.assertIsInstance(item, tuple)
            self.assertEqual(len(item), 5)


# ---------------------------------------------------------------------------
# SIMDJSON_AVAILABLE flag
# ---------------------------------------------------------------------------


class TestSimdjsonAvailableFlag(unittest.TestCase):
    def test_flag_is_bool(self) -> None:
        self.assertIsInstance(SIMDJSON_AVAILABLE, bool)

    def test_flag_exported_from_parsers_compat_shim(self) -> None:
        from ingestion.parsers import SIMDJSON_AVAILABLE as shim_flag

        self.assertIsInstance(shim_flag, bool)
        self.assertIs(shim_flag, SIMDJSON_AVAILABLE)


# ---------------------------------------------------------------------------
# Fallback-path coverage: monkeypatch _simd_decode → json.loads
# ---------------------------------------------------------------------------


class TestFallbackPath(unittest.TestCase):
    """Verify the pipeline is correct even when the SIMD back-end is absent."""

    def _run_with_stdlib_fallback(self, raw: bytes) -> object:
        """Patch _simd_decode to use stdlib json.loads and call parse_raw_pack."""
        with unittest.mock.patch.object(
            _parser_mod, "_simd_decode", side_effect=lambda b: json.loads(b)
        ):
            return parse_raw_pack(raw)

    def test_single_frame_via_fallback(self) -> None:
        raw = json.dumps(
            {"asset": "xlm-usd", "price": 0.105, "timestamp": 9999}
        ).encode()
        result = self._run_with_stdlib_fallback(raw)
        self.assertEqual(result, (("XLM-USD", 0.105, 9999, 0, 0),))

    def test_array_of_frames_via_fallback(self) -> None:
        frames = [
            {"asset": "ngn-xlm", "price": 0.005, "timestamp": 1},
            {"asset": "kes-xlm", "price": 0.007, "timestamp": 2},
        ]
        raw = json.dumps(frames).encode()
        result = self._run_with_stdlib_fallback(raw)
        self.assertEqual(
            result,
            (
                ("NGN-XLM", 0.005, 1, 0, 0),
                ("KES-XLM", 0.007, 2, 0, 0),
            ),
        )

    def test_fallback_honours_drop_invalid(self) -> None:
        frames = [
            {"asset": "xlm-usd", "price": 0.11, "timestamp": 1},
            {"asset": "missing-price", "timestamp": 2},
        ]
        raw = json.dumps(frames).encode()
        with unittest.mock.patch.object(
            _parser_mod, "_simd_decode", side_effect=lambda b: json.loads(b)
        ):
            result = parse_raw_pack(raw, drop_invalid=True)
        self.assertEqual(result, (("XLM-USD", 0.11, 1, 0, 0),))


# ---------------------------------------------------------------------------
# Compatibility shim — parsers.py re-exports
# ---------------------------------------------------------------------------


class TestParsersCompatShim(unittest.TestCase):
    """Ensure ingestion.parsers re-exports every name in ingestion.parser.__all__."""

    def test_all_names_re_exported(self) -> None:
        import ingestion.parser as mod
        import ingestion.parsers as shim

        for name in mod.__all__:
            self.assertTrue(
                hasattr(shim, name),
                msg=f"ingestion.parsers is missing re-export: {name!r}",
            )

    def test_parse_raw_pack_accessible_from_shim(self) -> None:
        from ingestion.parsers import parse_raw_pack as shim_fn

        raw = json.dumps({"asset": "btc-usd", "price": 64000.0, "timestamp": 1}).encode()
        result = shim_fn(raw)
        self.assertEqual(result, (("BTC-USD", 64000.0, 1, 0, 0),))


# ---------------------------------------------------------------------------
# Streaming tokenizer tests
# ---------------------------------------------------------------------------


def _to_stream(obj: object) -> io.BytesIO:
    """Serialise *obj* to JSON and wrap it in a BytesIO stream."""
    return io.BytesIO(json.dumps(obj).encode())


class StreamingParserTests(unittest.TestCase):
    """Tests for iter_price_events_from_stream / build_segments_from_stream."""

    # ------------------------------------------------------------------
    # iter_price_events_from_stream
    # ------------------------------------------------------------------

    def test_stream_single_bare_frame(self) -> None:
        """A bare frame object at the root is yielded directly."""
        payload = {"asset_id": "xlm-usd", "price": 0.1025, "timestamp": 1710000000}
        result = list(iter_price_events_from_stream(_to_stream(payload)))
        self.assertEqual(result, [("XLM-USD", 0.1025, 1710000000, 0, 0)])

    def test_stream_bytes_input(self) -> None:
        """Raw bytes are accepted in addition to file-like objects."""
        payload = {"asset": "btc-usd", "price": 64000.0, "timestamp": 1}
        raw = json.dumps(payload).encode()
        result = list(iter_price_events_from_stream(raw))
        self.assertEqual(result, [("BTC-USD", 64000.0, 1, 0, 0)])

    def test_stream_array_of_frames(self) -> None:
        """A top-level JSON array of frame objects is fully consumed."""
        payload = [
            {"asset": "xlm-usd", "price": 0.11, "timestamp": 1},
            {"asset": "btc-usd", "price": 1.22, "timestamp": 2},
            {"asset": "eth-usd", "price": 2.33, "timestamp": 3},
        ]
        result = list(iter_price_events_from_stream(_to_stream(payload)))
        self.assertEqual(
            result,
            [
                ("XLM-USD", 0.11, 1, 0, 0),
                ("BTC-USD", 1.22, 2, 0, 0),
                ("ETH-USD", 2.33, 3, 0, 0),
            ],
        )

    def test_stream_frames_wrapper_key(self) -> None:
        """``frames`` batch key is unwrapped transparently."""
        payload = {
            "frames": [
                {"pair": "ngn-xlm", "value": "0.00045", "ts": 100, "nonce": 5, "flag_bits": "1"},
                {"pair": "kes-xlm", "value": "0.00031", "ts": 101},
            ]
        }
        result = list(iter_price_events_from_stream(_to_stream(payload)))
        self.assertEqual(
            result,
            [
                ("NGN-XLM", 0.00045, 100, 5, 1),
                ("KES-XLM", 0.00031, 101, 0, 0),
            ],
        )

    def test_stream_all_optional_fields_default_to_zero(self) -> None:
        """sequence and flags default to 0 when absent in the stream."""
        payload = {"symbol": "ghs-xlm", "last_price": "0.00020", "event_time": 999}
        result = list(iter_price_events_from_stream(_to_stream(payload)))
        self.assertEqual(result, [("GHS-XLM", 0.0002, 999, 0, 0)])

    def test_stream_string_numeric_fields_are_coerced(self) -> None:
        """Numeric fields encoded as JSON strings are coerced to the right types."""
        payload = {
            "asset_id": "eth-usd",
            "price": "3150.75",
            "timestamp": "1710000000999",
            "sequence": "7",
            "flags": "2",
        }
        result = list(iter_price_events_from_stream(_to_stream(payload)))
        self.assertEqual(result, [("ETH-USD", 3150.75, 1710000000999, 7, 2)])

    def test_stream_drop_invalid_skips_bad_frames(self) -> None:
        """Frames missing required fields are skipped when drop_invalid=True."""
        payload = [
            {"asset": "xlm-usd", "price": 0.11, "timestamp": 1},   # valid
            {"asset": "bad-frame", "timestamp": 2},                  # missing price
            {"asset": "eth-usd", "price": "3.50", "time": "3"},      # valid
        ]
        result = list(
            iter_price_events_from_stream(_to_stream(payload), drop_invalid=True)
        )
        self.assertEqual(
            result,
            [
                ("XLM-USD", 0.11, 1, 0, 0),
                ("ETH-USD", 3.5, 3, 0, 0),
            ],
        )

    def test_stream_matches_in_memory_parser_output(self) -> None:
        """Streaming and in-memory parsers produce identical results."""
        frames = [
            {"asset_id": "xlm-usd", "price": 0.1025, "timestamp": 1710000000123, "seq": 101},
            {"asset_id": "btc-usd", "price": 64000.25, "timestamp": 1710000000456, "flags": 3},
            {"pair": "eth-usd", "value": "3150.75", "ts": 1710000000999, "nonce": 7, "flag_bits": "2"},
        ]
        # in-memory path
        expected = flatten_telemetry_frames([frames])
        # streaming path
        streamed = tuple(iter_price_events_from_stream(_to_stream(frames)))
        self.assertEqual(streamed, expected)

    # ------------------------------------------------------------------
    # build_segments_from_stream
    # ------------------------------------------------------------------

    def test_build_segments_from_stream_groups_into_fixed_size_batches(self) -> None:
        payload = [
            {"asset": "xlm-usd", "price": 0.11, "timestamp": 1},
            {"asset": "btc-usd", "price": 1.22, "timestamp": 2},
            {"asset": "eth-usd", "price": 2.33, "timestamp": 3},
        ]
        result = build_segments_from_stream(_to_stream(payload), segment_size=2)
        self.assertEqual(
            result,
            (
                (("XLM-USD", 0.11, 1, 0, 0), ("BTC-USD", 1.22, 2, 0, 0)),
                (("ETH-USD", 2.33, 3, 0, 0),),
            ),
        )

    def test_build_segments_from_stream_rejects_non_positive_segment_size(self) -> None:
        with self.assertRaisesRegex(ValueError, "segment_size"):
            build_segments_from_stream(io.BytesIO(b"[]"), segment_size=0)

    def test_build_segments_from_stream_empty_source_returns_empty_batch(self) -> None:
        result = build_segments_from_stream(_to_stream([]))
        self.assertEqual(result, ())

    def test_build_segments_from_stream_single_segment_when_fewer_than_size(self) -> None:
        payload = [{"asset": "xlm-usd", "price": 0.5, "timestamp": 1}]
        result = build_segments_from_stream(_to_stream(payload), segment_size=10)
        self.assertEqual(result, ((("XLM-USD", 0.5, 1, 0, 0),),))

    def test_build_segments_matches_in_memory_segments(self) -> None:
        """Streaming segments equal in-memory segments for the same data."""
        frames = [
            {"asset": "xlm-usd", "price": 0.11, "timestamp": i} for i in range(7)
        ]
        in_memory = build_telemetry_segments([frames], segment_size=3)
        streaming = build_segments_from_stream(_to_stream(frames), segment_size=3)
        self.assertEqual(streaming, in_memory)


if __name__ == "__main__":
    _ = unittest.main()