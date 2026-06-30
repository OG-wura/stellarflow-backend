"""Compiled Protocol Buffer blueprints for StellarFlow internal channels.

Every message that crosses microservice boundaries now has a strict structural
profile defined as a compiled protobuf message class.  The schemas are compiled
ahead-of-time from ``proto/stellarflow_channels.proto`` and imported as
``stellarflow_channels_pb2``.  This module exposes a thin, typed serialisation
facade that replaces the previous ad-hoc dictionary string representations.

Performance guarantees
----------------------
* Wire format: protobuf-encoded binary (no JSON overhead).
* Schema reuse: compiled descriptor pool is reused for every serialise /
  deserialise cycle.
* Type safety: callers receive typed message instances, not ``dict`` views.
"""

from __future__ import annotations

import logging
from typing import Type, TypeVar

from google.protobuf import message as _message

from serialization import stellarflow_channels_pb2 as _pb2
from serialization.encoders import TelemetryFrame as LegacyFrame

_T = TypeVar("_T", bound=_message.Message)

logger = logging.getLogger(__name__)

__all__ = [
    "ProtoBroker",
    # Compiled message classes (re-exported for convenience)
    "PriceUpdate",
    "SignatureRequest",
    "SignatureResponse",
    "TelemetryBundle",
    "TelemetryFrame",
    "Heartbeat",
    "MessageEnvelope",
    # Singleton convenience functions
    "serialize",
    "deserialize",
    "wrap",
    "unwrap",
    "new_price_update",
    "frame_to_proto",
    "bundle_legacy_frames",
]

# Re-export compiled message classes so callers can type-annotate against them
# directly without reaching into the pb2 module.
PriceUpdate = _pb2.PriceUpdate
SignatureRequest = _pb2.SignatureRequest
SignatureResponse = _pb2.SignatureResponse
TelemetryBundle = _pb2.TelemetryBundle
TelemetryFrame = _pb2.TelemetryFrame
Heartbeat = _pb2.Heartbeat
MessageEnvelope = _pb2.MessageEnvelope


# ---------------------------------------------------------------------------
# Broker -- typed serialise / deserialise facade
# ---------------------------------------------------------------------------
class ProtoBroker:
    """Ultra-light serialisation broker backed by compiled protobuf blueprints.

    Every message type accepted by the broker has a fixed structural profile
    (the compiled ``Message`` subclass). Serialising returns a binary byte
    string; deserialising requires an explicit target type so the broker can
    route to the correct parser.

    Usage::

        broker = ProtoBroker()
        msg = PriceUpdate()
        msg.asset_id = "NGN/XLM"
        msg.price = 15000000
        wire = broker.serialize(msg)
        parsed = broker.deserialize(wire, PriceUpdate)
    """

    def __init__(self) -> None:
        self._registry: dict[Type[_message.Message], tuple[str, str]] = {}

    # ------------------------------------------------------------------
    # Registration (optional explicit -- auto-detected when serialising)
    # ------------------------------------------------------------------
    def register(
        self,
        msg_cls: Type[_message.Message],
        channel: str,
        content_type: str,
    ) -> None:
        """Register a message class for a named channel.

        Args:
            msg_cls:      A compiled protobuf Message class.
            channel:      Logical channel name (e.g. ``"price.feed"``).
            content_type: MIME-style type string (e.g. ``"application/x-protobuf"``).
        """
        self._registry[msg_cls] = (channel, content_type)

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------
    def serialize(self, msg: _message.Message, channel: str | None = None) -> bytes:
        """Serialise a compiled protobuf message to a compact binary buffer.

        Args:
            msg:     A populated protobuf Message instance.

        Returns:
            Raw binary bytes ready for socket / queue transmission.
        """
        if not isinstance(msg, _message.Message):
            raise TypeError(
                f"Expected a protobuf Message instance, got {type(msg).__name__}"
            )
        return msg.SerializeToString()

    # ------------------------------------------------------------------
    # Deserialisation
    # ------------------------------------------------------------------
    def deserialize(self, data: bytes, msg_cls: Type[_T]) -> _T:
        """Deserialise a binary buffer into a typed protobuf Message instance.

        Args:
            data:    Raw bytes previously produced by :meth:`serialize`.
            msg_cls: The target compiled Message class.

        Returns:
            A populated instance of ``msg_cls``.

        Raises:
            google.protobuf.message.DecodeError: If the wire format is corrupt.
        """
        msg = msg_cls()
        msg.ParseFromString(data)
        return msg

    # ------------------------------------------------------------------
    # High-level envelope helpers
    # ------------------------------------------------------------------
    def wrap(self, msg: _message.Message, source: str = "stellarflow") -> MessageEnvelope:
        """Wrap a typed message inside a ``MessageEnvelope`` for routing.

        The inner message is protobuf-encoded into the envelope's ``payload``
        field. The ``channel`` and ``content_type`` are looked up from the
        broker's registry, falling back to sensible defaults.

        Args:
            msg:    A populated protobuf Message instance.
            source: Logical source identifier (default ``"stellarflow"``).

        Returns:
            A :class:`MessageEnvelope` ready for transport.
        """
        channel, content_type = self._registry.get(
            type(msg),
            ("stellarflow.generic", "application/x-protobuf"),
        )
        envelope = MessageEnvelope()
        envelope.channel = channel
        envelope.content_type = content_type
        envelope.payload = self.serialize(msg)
        envelope.timestamp = 0  # caller may override
        envelope.source = source
        return envelope

    def unwrap(self, envelope: MessageEnvelope, msg_cls: Type[_T]) -> _T:
        """Extract and deserialise the payload from a ``MessageEnvelope``.

        Args:
            envelope: A :class:`MessageEnvelope` received from a channel.
            msg_cls:  The expected inner message type.

        Returns:
            The deserialised inner message.
        """
        return self.deserialize(envelope.payload, msg_cls)

    # ------------------------------------------------------------------
    # Convenience factory methods
    # ------------------------------------------------------------------
    def new_price_update(
        self,
        asset_id: str,
        price: int,
        volume: int = 0,
        timestamp: int = 0,
        sequence: int = 0,
        flags: int = 0,
        feed_id: int = 0,
    ) -> PriceUpdate:
        """Create a populated :class:`PriceUpdate` message.

        All numeric arguments are stored as fixed-point integers.
        """
        msg = PriceUpdate()
        msg.asset_id = asset_id
        msg.price = price
        msg.volume = volume
        msg.timestamp = timestamp
        msg.sequence = sequence
        msg.flags = flags
        msg.feed_id = feed_id
        return msg

    def frame_to_proto(self, frame: LegacyFrame) -> TelemetryFrame:  # type: ignore[override]
        """Convert a ``struct``-based :class:`TelemetryFrame` into its protobuf twin.

        This is a zero-copy-style mapping: numeric fields are assigned directly,
        and the ``asset_id`` bytes payload is transferred without re-encoding.
        """
        if not isinstance(frame, LegacyFrame):
            raise TypeError(f"Expected TelemetryFrame, got {type(frame).__name__}")

        pf = TelemetryFrame()
        pf.asset_id = frame.asset_id
        pf.price = frame.price
        pf.volume = frame.volume
        pf.timestamp = frame.timestamp
        pf.sequence = frame.sequence
        pf.flags = frame.flags
        pf.feed_id = frame.feed_id
        return pf

    def bundle_legacy_frames(self, frames: list[LegacyFrame]) -> TelemetryBundle:
        """Pack a list of legacy ``TelemetryFrame`` objects into a protobuf bundle.

        Returns a :class:`TelemetryBundle` with ``bundle_sequence`` set to the
        frame count.
        """
        bundle = TelemetryBundle()
        bundle.bundle_sequence = len(frames)
        for frame in frames:
            bundle.frames.append(self.frame_to_proto(frame))
        return bundle


# ---------------------------------------------------------------------------
# Singleton facade
# ---------------------------------------------------------------------------
_broker = ProtoBroker()

serialize = _broker.serialize
deserialize = _broker.deserialize
wrap = _broker.wrap
unwrap = _broker.unwrap
new_price_update = _broker.new_price_update
frame_to_proto = _broker.frame_to_proto
bundle_legacy_frames = _broker.bundle_legacy_frames
