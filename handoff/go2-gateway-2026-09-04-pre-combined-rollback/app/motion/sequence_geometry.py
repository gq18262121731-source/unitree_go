from __future__ import annotations

import math
from dataclasses import asdict, dataclass

from app.motion.action_sequence import MotionSequence


@dataclass(frozen=True)
class PlannedPose:
    step: int
    action: str
    x_m: float
    y_m: float
    heading_deg: float

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class SequenceGeometry:
    poses: tuple[PlannedPose, ...]
    min_x_m: float
    max_x_m: float
    min_y_m: float
    max_y_m: float

    def to_dict(self, margin_m: float = 0.0) -> dict[str, object]:
        return {
            "poses": [pose.to_dict() for pose in self.poses],
            "pathBoundsM": {
                "minX": self.min_x_m,
                "maxX": self.max_x_m,
                "minY": self.min_y_m,
                "maxY": self.max_y_m,
                "width": self.max_x_m - self.min_x_m,
                "height": self.max_y_m - self.min_y_m,
            },
            "clearAreaWithMarginM": {
                "marginEachSide": margin_m,
                "minX": self.min_x_m - margin_m,
                "maxX": self.max_x_m + margin_m,
                "minY": self.min_y_m - margin_m,
                "maxY": self.max_y_m + margin_m,
                "width": self.max_x_m - self.min_x_m + 2.0 * margin_m,
                "height": self.max_y_m - self.min_y_m + 2.0 * margin_m,
            },
        }


def plan_sequence_geometry(sequence: MotionSequence) -> SequenceGeometry:
    """Compute ideal planar waypoints in the initial robot frame.

    This is an offline clearance estimate only. It intentionally does not
    model odometry error, chassis footprint, gait sway, or stopping drift.
    """

    x = 0.0
    y = 0.0
    heading = 0.0
    poses = [PlannedPose(0, "start", x, y, math.degrees(heading))]
    for index, step in enumerate(sequence.steps, start=1):
        action = step.action
        parameters = step.parameters
        if action in {"turn_left", "turn_right", "turn_clockwise"}:
            angle = math.radians(float(parameters["angle_deg"]))
            heading += angle if action == "turn_left" else -angle
            heading = math.atan2(math.sin(heading), math.cos(heading))
        elif action in {"forward", "backward", "move_left", "move_right"}:
            distance = float(parameters["distance_m"])
            if action == "backward":
                distance = -distance
                direction = heading
            elif action == "move_left":
                direction = heading + math.pi / 2.0
            elif action == "move_right":
                direction = heading - math.pi / 2.0
            else:
                direction = heading
            x += distance * math.cos(direction)
            y += distance * math.sin(direction)
        poses.append(PlannedPose(index, action, x, y, math.degrees(heading)))
    xs = [pose.x_m for pose in poses]
    ys = [pose.y_m for pose in poses]
    return SequenceGeometry(
        poses=tuple(poses),
        min_x_m=min(xs),
        max_x_m=max(xs),
        min_y_m=min(ys),
        max_y_m=max(ys),
    )
