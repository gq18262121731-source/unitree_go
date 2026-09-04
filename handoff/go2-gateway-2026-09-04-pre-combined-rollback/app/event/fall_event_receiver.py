from __future__ import annotations

from app.schemas.tasks import FallEventRequest
from app.task_manager.robot_task_manager import RobotTaskManager


class FallEventReceiver:
    def __init__(self, task_manager: RobotTaskManager) -> None:
        self.task_manager = task_manager

    def receive(self, event: FallEventRequest) -> dict:
        return self.task_manager.create_confirm_fall_task(event)
