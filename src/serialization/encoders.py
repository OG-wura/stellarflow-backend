from __future__ import annotations

"""
src/serialization/encoders.py
==============================
FlatBuffers-based encoder for StellarFlow telemetry bundles.
Converts high-frequency structural metrics arrays into compact binary buffers
using FlatBuffers, eliminating CPU serialization overhead and allowing direct
reading of packed attributes without unpacking loops.
"""

import flatbuffers
from typing import NamedTuple, Sequence

# Import generated FlatBuffers code
from src.serialization.stellarflow.TelemetryFrame import (
    TelemetryFrame as FlatTelemetryFrame,
    TelemetryFrameStart,
    TelemetryFrameAddAssetId,
    TelemetryFrameAddPrice,
    TelemetryFrameAddVolume,
    TelemetryFrameAddTimestamp,
    TelemetryFrameAddSequence,
    TelemetryFrameAddFlags,
    TelemetryFrameAddFeedId,
    TelemetryFrameEnd,
    TelemetryFrameStartAssetIdVector,
)
from src.serialization.stellarflow.TelemetryBundle import (
    TelemetryBundle as FlatTelemetryBundle,
    TelemetryBundleStart,
    TelemetryBundleAddFrames,
    TelemetryBundleEnd,
    TelemetryBundleStartFramesVector,
)
from src.serialization.stellarflow.Flags import Flags

FLAG_LIVE = Flags.LIVE
FLAG_STALE = Flags.STALE
FLAG_ANOMALY = Flags.ANOMALY
FLAG_SYNTHETIC = Flags.SYNTHETIC
FLAG_HALTED = Flags.HALTED


# Typed data container (keeping the same API as before)
class TelemetryFrame(NamedTuple):
    """Immutable typed container for a single compacted telemetry record."""
    asset_id: bytes
    price: int
    volume: int
    timestamp: int
    sequence: int
    flags: int
    feed_id: int


def pack_frame(frame: TelemetryFrame) -> bytes:
    """Serialize a single TelemetryFrame into a FlatBuffers buffer."""
    builder = flatbuffers.Builder(0)
    
    # Create asset_id vector
    asset_bytes = frame.asset_id
    asset_offset = builder.CreateByteVector(asset_bytes)
    
    # Build the TelemetryFrame
    TelemetryFrameStart(builder)
    TelemetryFrameAddAssetId(builder, asset_offset)
    TelemetryFrameAddPrice(builder, frame.price)
    TelemetryFrameAddVolume(builder, frame.volume)
    TelemetryFrameAddTimestamp(builder, frame.timestamp)
    TelemetryFrameAddSequence(builder, frame.sequence)
    TelemetryFrameAddFlags(builder, frame.flags)
    TelemetryFrameAddFeedId(builder, frame.feed_id)
    frame_offset = TelemetryFrameEnd(builder)
    
    builder.Finish(frame_offset)
    return bytes(builder.Output())


def unpack_frame(data: bytes) -> TelemetryFrame:
    """Deserialize a FlatBuffers buffer into a TelemetryFrame."""
    buf = bytearray(data)
    flat_frame = FlatTelemetryFrame.GetRootAs(buf, 0)
    
    # Extract asset_id
    asset_len = flat_frame.AssetIdLength()
    asset_id = bytes([flat_frame.AssetId(i) for i in range(asset_len)])
    
    return TelemetryFrame(
        asset_id=asset_id,
        price=flat_frame.Price(),
        volume=flat_frame.Volume(),
        timestamp=flat_frame.Timestamp(),
        sequence=flat_frame.Sequence(),
        flags=flat_frame.Flags(),
        feed_id=flat_frame.FeedId(),
    )


def pack_bundle(frames: Sequence[TelemetryFrame]) -> bytes:
    """Serialize a sequence of TelemetryFrames into a FlatBuffers buffer."""
    builder = flatbuffers.Builder(0)
    
    # Create all frame offsets in reverse order (FlatBuffers requirement)
    frame_offsets = []
    for frame in reversed(frames):
        # Create asset_id vector
        asset_bytes = frame.asset_id
        asset_offset = builder.CreateByteVector(asset_bytes)
        
        # Build the TelemetryFrame
        TelemetryFrameStart(builder)
        TelemetryFrameAddAssetId(builder, asset_offset)
        TelemetryFrameAddPrice(builder, frame.price)
        TelemetryFrameAddVolume(builder, frame.volume)
        TelemetryFrameAddTimestamp(builder, frame.timestamp)
        TelemetryFrameAddSequence(builder, frame.sequence)
        TelemetryFrameAddFlags(builder, frame.flags)
        TelemetryFrameAddFeedId(builder, frame.feed_id)
        frame_offsets.append(TelemetryFrameEnd(builder))
    
    # Reverse to get the original order
    frame_offsets.reverse()
    
    # Build the frames vector
    TelemetryBundleStartFramesVector(builder, len(frame_offsets))
    for offset in reversed(frame_offsets):
        builder.PrependUOffsetTRelative(offset)
    frames_vector = builder.EndVector()
    
    # Build the TelemetryBundle
    TelemetryBundleStart(builder)
    TelemetryBundleAddFrames(builder, frames_vector)
    bundle_offset = TelemetryBundleEnd(builder)
    
    builder.Finish(bundle_offset)
    return bytes(builder.Output())


def unpack_bundle(data: bytes) -> list[TelemetryFrame]:
    """Deserialize a FlatBuffers buffer into a list of TelemetryFrames."""
    buf = bytearray(data)
    flat_bundle = FlatTelemetryBundle.GetRootAs(buf, 0)
    
    frames = []
    num_frames = flat_bundle.FramesLength()
    for i in range(num_frames):
        flat_frame = flat_bundle.Frames(i)
        if flat_frame is None:
            continue
        
        # Extract asset_id
        asset_len = flat_frame.AssetIdLength()
        asset_id = bytes([flat_frame.AssetId(j) for j in range(asset_len)])
        
        frames.append(TelemetryFrame(
            asset_id=asset_id,
            price=flat_frame.Price(),
            volume=flat_frame.Volume(),
            timestamp=flat_frame.Timestamp(),
            sequence=flat_frame.Sequence(),
            flags=flat_frame.Flags(),
            feed_id=flat_frame.FeedId(),
        ))
    return frames


def bundle_frame_count(data: bytes) -> int:
    """Return the number of frames present in a FlatBuffers bundle buffer."""
    try:
        buf = bytearray(data)
        flat_bundle = FlatTelemetryBundle.GetRootAs(buf, 0)
        return flat_bundle.FramesLength()
    except Exception:
        return 0


def encode_asset_id(symbol: str) -> bytes:
    """Encode a human-readable asset-pair symbol into bytes."""
    return symbol.encode("ascii", errors="replace")


def decode_asset_id(asset_bytes: bytes) -> str:
    """Decode asset_id bytes back into a readable string."""
    return asset_bytes.decode("ascii", errors="replace")


# Public API
__all__ = [
    "TelemetryFrame",
    "FLAG_LIVE",
    "FLAG_STALE",
    "FLAG_ANOMALY",
    "FLAG_SYNTHETIC",
    "FLAG_HALTED",
    "pack_frame",
    "unpack_frame",
    "pack_bundle",
    "unpack_bundle",
    "bundle_frame_count",
    "encode_asset_id",
    "decode_asset_id",
]
