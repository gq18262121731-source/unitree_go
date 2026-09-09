from __future__ import annotations

from dataclasses import dataclass


@dataclass
class InteractionContext:
    health_assessment_valid: bool = False
    medication_reminded: bool = False
    medication_acknowledged: bool = False
    medication_taken: bool = False
    departure_confirmed: bool = False
    outing_state: str = "idle"
    safety_state: str = "normal"
    fall_user_response: str | None = None
    fall_visual_recovered: bool = False
    expected_reply: str | None = None
    expected_reply_deadline: float | None = None

    def set_expected_reply(self, value: str, *, now: float, timeout_seconds: float) -> None:
        self.expected_reply = value
        self.expected_reply_deadline = now + max(1.0, float(timeout_seconds))

    def clear_expected_reply(self) -> None:
        self.expected_reply = None
        self.expected_reply_deadline = None

    def expire_expected_reply(self, *, now: float) -> bool:
        if self.expected_reply_deadline is None or now < self.expected_reply_deadline:
            return False
        self.clear_expected_reply()
        return True

    def reset_outing_reply_window(self) -> None:
        self.outing_state = "idle"
        self.clear_expected_reply()

    def reset_demo(self) -> None:
        self.health_assessment_valid = False
        self.medication_reminded = False
        self.medication_acknowledged = False
        self.medication_taken = False
        self.departure_confirmed = False
        self.outing_state = "idle"
        self.safety_state = "normal"
        self.fall_user_response = None
        self.fall_visual_recovered = False
        self.clear_expected_reply()
