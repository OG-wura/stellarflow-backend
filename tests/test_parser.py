from __future__ import annotations

import io
import json
import unittest

from ingestion.parser import (
    build_segments_from_stream,
    build_telemetry_segments,
    flatten_telemetry_frames,
    iter_price_events_from_stream,
)


class ParserTests(unittest.TestCase):
    def test_flatten_telemetry_frames_collapses_nested_websocket_batches(self) -> None:
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

    def test_build_telemetry_segments_groups_frames_into_fixed_size_batches(
        self,
    ) -> None:
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

    def test_flatten_telemetry_frames_can_skip_invalid_payloads(self) -> None:
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

    def test_build_telemetry_segments_rejects_non_positive_segment_size(self) -> None:
        with self.assertRaisesRegex(ValueError, "segment_size"):
            _ = build_telemetry_segments([], segment_size=0)


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