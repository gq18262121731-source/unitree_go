from __future__ import annotations

import time
import unittest

import app as bridge


def make_frame() -> bridge.Frame:
    return bridge.Frame(
        jpeg=b"jpeg",
        sequence=7,
        captured_at="2026-07-19T14:00:00+08:00",
        width=1280,
        height=720,
        fps=8.2,
    )


class StatusContractTests(unittest.TestCase):
    def test_ready_status_keeps_legacy_fields_and_adds_v1_fields(self) -> None:
        camera = bridge.WirelessCamera()
        with camera._lock:
            camera._started_monotonic = time.monotonic() - 5
            camera._started_at = "2026-07-19T13:59:55+08:00"
            camera._connected = True
            camera._frame = make_frame()
            camera._last_frame_monotonic = time.monotonic()

        status = camera.status()

        self.assertTrue(status["connected"])
        self.assertTrue(status["hasFrame"])
        self.assertEqual(status["videoState"], "ready")
        self.assertEqual(status["lastFrameAt"], "2026-07-19T14:00:00+08:00")
        self.assertEqual(status["resolution"], {"width": 1280, "height": 720})
        self.assertEqual(status["source"]["device"], "Go2")
        self.assertIsNone(status["lastErrorCode"])
        self.assertIn("captureFps", status)
        self.assertIn("latestFrame", status)

    def test_stale_frame_has_stable_error_code(self) -> None:
        camera = bridge.WirelessCamera()
        with camera._lock:
            camera._started_monotonic = time.monotonic() - 10
            camera._connected = True
            camera._frame = make_frame()
            camera._last_frame_monotonic = time.monotonic() - bridge.FRAME_STALE_SECONDS - 0.5

        status = camera.status()

        self.assertFalse(status["hasFrame"])
        self.assertEqual(status["videoState"], "stalled")
        self.assertEqual(status["lastErrorCode"], "FRAME_STALLED")

    def test_connected_without_frame_reports_timeout(self) -> None:
        camera = bridge.WirelessCamera()
        with camera._lock:
            camera._started_monotonic = time.monotonic() - bridge.FRAME_STALE_SECONDS - 0.5
            camera._connected = True

        status = camera.status()

        self.assertFalse(status["hasFrame"])
        self.assertEqual(status["videoState"], "no-frame")
        self.assertEqual(status["lastErrorCode"], "NO_FRAME_TIMEOUT")

    def test_client_count_is_bounded_at_zero(self) -> None:
        camera = bridge.WirelessCamera()
        camera.register_client()
        camera.unregister_client()
        camera.unregister_client()
        self.assertEqual(camera.status()["clientCount"], 0)


if __name__ == "__main__":
    unittest.main()
