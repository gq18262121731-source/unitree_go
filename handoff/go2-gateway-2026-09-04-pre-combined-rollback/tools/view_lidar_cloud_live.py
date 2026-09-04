#!/usr/bin/env python3
"""Display the Go2 L1 ``cloud_base`` stream in a read-only local window.

The viewer creates one CycloneDDS DataReader and no writers.  It does not
import or initialize Unitree motion clients, RobotService, or the gateway.
Close the Matplotlib window to stop the subscriber.
"""

from __future__ import annotations

import argparse
import math
import socket
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.probe_lidar_safety_phase7_1c import cyclone_config, decode_xyz


TOPIC_CLOUD_BASE = "rt/utlidar/cloud_base"


def auto_local_address(peer: str) -> str:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        probe.connect((peer, 7400))
        return str(probe.getsockname()[0])


def display_stride(point_count: int, max_points: int) -> int:
    """Return a deterministic stride that never exceeds max_points."""

    if point_count < 0:
        raise ValueError("point_count must not be negative")
    if max_points < 1:
        raise ValueError("max_points must be positive")
    return max(1, math.ceil(point_count / max_points))


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--peer", default="192.168.123.161")
    parser.add_argument("--local-address")
    parser.add_argument("--interface")
    parser.add_argument("--domain", type=int, default=0)
    parser.add_argument("--topic", default=TOPIC_CLOUD_BASE)
    parser.add_argument("--max-points", type=int, default=8000)
    parser.add_argument("--interval-ms", type=int, default=80)
    parser.add_argument("--x-min", type=float, default=-1.0)
    parser.add_argument("--x-max", type=float, default=5.0)
    parser.add_argument("--y-min", type=float, default=-3.0)
    parser.add_argument("--y-max", type=float, default=3.0)
    parser.add_argument("--z-min", type=float, default=-1.0)
    parser.add_argument("--z-max", type=float, default=2.0)
    args = parser.parse_args(argv)
    if args.max_points < 1:
        parser.error("--max-points must be positive")
    if args.interval_ms < 20:
        parser.error("--interval-ms must be at least 20")
    if args.interface and args.local_address:
        parser.error("--interface and --local-address are mutually exclusive")
    for lower, upper, label in (
        (args.x_min, args.x_max, "x"),
        (args.y_min, args.y_max, "y"),
        (args.z_min, args.z_max, "z"),
    ):
        if not math.isfinite(lower) or not math.isfinite(upper) or lower >= upper:
            parser.error(f"invalid {label} display limits")
    return args


def run(args: argparse.Namespace) -> int:
    # Import graphical and DDS dependencies only when the tool is executed.
    # QPainter is intentionally used instead of Matplotlib/OpenGL so the viewer
    # does not depend on NumPy's compiled extension ABI.
    from PyQt5.QtCore import QPointF, QTimer, Qt
    from PyQt5.QtGui import QColor, QFont, QPainter, QPen
    from PyQt5.QtWidgets import QApplication, QWidget
    from cyclonedds.domain import Domain, DomainParticipant
    from cyclonedds.sub import DataReader
    from cyclonedds.topic import Topic
    from unitree_sdk2py.idl.sensor_msgs.msg.dds_ import PointCloud2_

    local_address = args.local_address
    if not args.interface and not local_address:
        local_address = auto_local_address(args.peer)
    config_xml = cyclone_config(
        peer=args.peer,
        interface=args.interface,
        local_address=local_address,
    )

    configured_domain = Domain(args.domain, config_xml)
    participant = DomainParticipant(args.domain)
    reader = DataReader(
        participant,
        Topic(participant, args.topic, PointCloud2_),
    )

    class CloudCanvas(QWidget):
        def __init__(self) -> None:
            super().__init__()
            self.setWindowTitle("Go2 L1 Live Point Cloud - READ ONLY")
            self.resize(1100, 780)
            self.setMinimumSize(720, 520)
            self.setStyleSheet("background-color: #081018;")
            self.points: list[tuple[float, float, float]] = []
            self.frames = 0
            self.started = time.monotonic()
            self.last_frame: float | None = None
            self.frame_id = ""
            self.error: str | None = None
            self.azimuth = math.radians(-45.0)
            self.elevation = math.radians(20.0)
            self.zoom = 1.0
            self.drag_at = None
            self.timer = QTimer(self)
            self.timer.timeout.connect(self.read_cloud)
            self.timer.start(args.interval_ms)

        def read_cloud(self) -> None:
            try:
                samples = list(reader.take(16) or [])
                if samples:
                    decoded = decode_xyz(samples[-1])
                    stride = display_stride(len(decoded), args.max_points)
                    self.points = decoded[::stride]
                    self.frames += len(samples)
                    self.last_frame = time.monotonic()
                    self.frame_id = str(
                        getattr(getattr(samples[-1], "header", None), "frame_id", "")
                    )
                self.update()
            except Exception as exc:  # Keep the error visible in the window.
                self.error = str(exc)
                self.timer.stop()
                self.update()

        def _project(self, point: tuple[float, float, float]) -> tuple[float, float, float]:
            x, y, z = point
            ca, sa = math.cos(self.azimuth), math.sin(self.azimuth)
            ce, se = math.cos(self.elevation), math.sin(self.elevation)
            x1 = ca * x - sa * y
            y1 = sa * x + ca * y
            depth = ce * x1 + se * z
            vertical = -se * x1 + ce * z
            return y1, vertical, depth

        def _screen(self, projected: tuple[float, float, float]) -> QPointF:
            horizontal, vertical, _depth = projected
            scale = min(self.width() / 8.0, self.height() / 5.5) * self.zoom
            return QPointF(
                self.width() * 0.50 + horizontal * scale,
                self.height() * 0.67 - vertical * scale,
            )

        def _height_color(self, z: float) -> QColor:
            ratio = (z - args.z_min) / (args.z_max - args.z_min)
            ratio = min(1.0, max(0.0, ratio))
            return QColor.fromHsvF((1.0 - ratio) * 0.68, 0.88, 1.0, 0.92)

        def paintEvent(self, _event: object) -> None:  # noqa: N802 - Qt API
            painter = QPainter(self)
            painter.setRenderHint(QPainter.Antialiasing, False)
            painter.fillRect(self.rect(), QColor("#081018"))

            visible = [
                point
                for point in self.points
                if args.x_min <= point[0] <= args.x_max
                and args.y_min <= point[1] <= args.y_max
                and args.z_min <= point[2] <= args.z_max
            ]
            projected = [(self._project(point), point[2]) for point in visible]
            projected.sort(key=lambda item: item[0][2])
            for coordinates, height in projected:
                painter.setPen(QPen(self._height_color(height), 2.0))
                painter.drawPoint(self._screen(coordinates))

            origin = self._screen(self._project((0.0, 0.0, 0.0)))
            for endpoint, color, label in (
                ((1.0, 0.0, 0.0), QColor("#ff5c57"), "X forward"),
                ((0.0, 1.0, 0.0), QColor("#63d471"), "Y left"),
                ((0.0, 0.0, 1.0), QColor("#4aa3ff"), "Z up"),
            ):
                target = self._screen(self._project(endpoint))
                painter.setPen(QPen(color, 3.0))
                painter.drawLine(origin, target)
                painter.drawText(target + QPointF(5.0, -5.0), label)

            painter.setFont(QFont("Segoe UI", 10))
            painter.setPen(QColor("#e8f1f8"))
            if self.error:
                status = f"Viewer error: {self.error}"
            elif self.last_frame is None:
                status = f"Waiting for {args.topic} ..."
            else:
                elapsed = max(time.monotonic() - self.started, 1e-6)
                age = time.monotonic() - self.last_frame
                status = (
                    f"{args.topic} | frame={self.frame_id} | {self.frames / elapsed:.1f} Hz | "
                    f"points={len(self.points)} | age={age:.2f}s"
                )
            painter.drawText(18, 28, status)
            painter.setPen(QColor("#8fd3ff"))
            painter.drawText(
                18,
                50,
                f"peer={args.peer}  local={local_address or args.interface}  READ ONLY  |  drag: rotate  wheel: zoom  close: stop",
            )
            painter.end()

        def mousePressEvent(self, event: object) -> None:  # noqa: N802 - Qt API
            if event.button() == Qt.LeftButton:
                self.drag_at = event.pos()

        def mouseMoveEvent(self, event: object) -> None:  # noqa: N802 - Qt API
            if self.drag_at is None:
                return
            delta = event.pos() - self.drag_at
            self.drag_at = event.pos()
            self.azimuth += delta.x() * 0.008
            self.elevation = min(
                math.radians(85.0),
                max(math.radians(-85.0), self.elevation + delta.y() * 0.008),
            )
            self.update()

        def mouseReleaseEvent(self, _event: object) -> None:  # noqa: N802 - Qt API
            self.drag_at = None

        def wheelEvent(self, event: object) -> None:  # noqa: N802 - Qt API
            self.zoom *= 1.12 if event.angleDelta().y() > 0 else 1.0 / 1.12
            self.zoom = min(6.0, max(0.25, self.zoom))
            self.update()

    application = QApplication.instance() or QApplication(sys.argv[:1])
    viewer = CloudCanvas()
    # Keep DDS resources alive for the full GUI lifetime.
    viewer._go2_dds_resources = (configured_domain, participant, reader)  # type: ignore[attr-defined]
    viewer.show()
    result = application.exec_()
    return 2 if viewer.error else int(result)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
