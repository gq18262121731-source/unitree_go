from __future__ import annotations

import pytest

from app.motion.action_sequence import load_motion_sequence
from app.motion.sequence_geometry import plan_sequence_geometry


def test_phone_demo_clockwise_geometry_and_clearance() -> None:
    sequence = load_motion_sequence("configs/phone_demo.yaml")
    geometry = plan_sequence_geometry(sequence)

    assert geometry.poses[2].heading_deg == pytest.approx(-90.0)
    assert geometry.poses[4].heading_deg == pytest.approx(165.0)
    assert geometry.poses[-1].x_m == pytest.approx(0.0740, abs=0.001)
    assert geometry.poses[-1].y_m == pytest.approx(-0.0596, abs=0.001)
    assert geometry.min_x_m == pytest.approx(-0.4557, abs=0.001)
    assert geometry.max_x_m == pytest.approx(0.8)
    assert geometry.min_y_m == pytest.approx(-1.6)
    assert geometry.max_y_m == pytest.approx(0.1595, abs=0.001)
    clearance = geometry.to_dict(margin_m=1.0)["clearAreaWithMarginM"]
    assert clearance["width"] == pytest.approx(3.2557, abs=0.001)
    assert clearance["height"] == pytest.approx(3.7595, abs=0.001)
