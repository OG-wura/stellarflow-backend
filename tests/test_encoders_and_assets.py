import unittest
import threading
import time
from src.serialization.encoders import TelemetryFrame, TelemetryEncoder, pack_frame, unpack_frame
from src.config.assets import AssetRegistry, global_assets
from src.network.router import validate_asset_pair

class TestTelemetryEncoder(unittest.TestCase):
    def test_pack_unpack_frame(self) -> None:
        frame = TelemetryFrame(
            asset_id=b"NGN/XLM",
            price=123456789,
            volume=987654321,
            timestamp=1625000000000,
            sequence=42,
            flags=1,
            feed_id=12,
        )

        packed = TelemetryEncoder.pack(frame)
        self.assertEqual(len(packed), 40)

        unpacked = TelemetryEncoder.unpack(packed)
        self.assertEqual(unpacked.asset_id, b"NGN/XLM")
        self.assertEqual(unpacked.price, 123456789)
        self.assertEqual(unpacked.volume, 987654321)
        self.assertEqual(unpacked.timestamp, 1625000000000)
        self.assertEqual(unpacked.sequence, 42)
        self.assertEqual(unpacked.flags, 1)
        self.assertEqual(unpacked.feed_id, 12)

    def test_legacy_functions(self) -> None:
        frame = TelemetryFrame(
            asset_id=b"USD/XLM",
            price=1000,
            volume=5000,
            timestamp=1625000000100,
            sequence=100,
            flags=2,
            feed_id=1,
        )
        packed = pack_frame(frame)
        unpacked = unpack_frame(packed)
        self.assertEqual(unpacked.asset_id, b"USD/XLM")

    def test_pack_unpack_bundle(self) -> None:
        frames = [
            TelemetryFrame(b"NGN/XLM", 100, 200, 1000, 1, 0, 1),
            TelemetryFrame(b"USD/XLM", 300, 400, 2000, 2, 0, 2),
        ]
        packed = TelemetryEncoder.pack_bundle(frames)
        self.assertEqual(len(packed), 80)

        unpacked = TelemetryEncoder.unpack_bundle(packed)
        self.assertEqual(len(unpacked), 2)
        self.assertEqual(unpacked[0].asset_id, b"NGN/XLM")
        self.assertEqual(unpacked[1].asset_id, b"USD/XLM")


class TestAssetRegistry(unittest.TestCase):
    def test_asset_registry_lookups(self) -> None:
        registry = AssetRegistry()
        self.assertEqual(registry.get_asset_name("USD"), "US Dollar")
        self.assertIsNone(registry.get_asset_name("INVALID"))

        registry.register_asset("INVALID", "Invalid Asset")
        self.assertEqual(registry.get_asset_name("INVALID"), "Invalid Asset")

        registry.remove_asset("INVALID")
        self.assertIsNone(registry.get_asset_name("INVALID"))

    def test_thread_safety(self) -> None:
        registry = AssetRegistry()
        errors = []

        def worker_read() -> None:
            try:
                for _ in range(100):
                    registry.get_asset_name("USD")
                    registry.get_all_assets()
                    time.sleep(0.001)
            except Exception as e:
                errors.append(e)

        def worker_write() -> None:
            try:
                for i in range(100):
                    registry.register_asset(f"NEW_{i}", f"New Asset {i}")
                    registry.remove_asset(f"NEW_{i}")
                    time.sleep(0.001)
            except Exception as e:
                errors.append(e)

        threads = []
        for _ in range(5):
            threads.append(threading.Thread(target=worker_read))
            threads.append(threading.Thread(target=worker_write))

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(errors), 0, f"Thread errors encountered: {errors}")

    def test_router_integration(self) -> None:
        global_assets.register_asset("TEST_CODE", "Test Asset Name")
        name = validate_asset_pair("TEST_CODE")
        self.assertEqual(name, "Test Asset Name")
        global_assets.remove_asset("TEST_CODE")
