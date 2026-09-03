from __future__ import annotations

from typing import Annotated
from typing import Literal

from pydantic import AliasChoices, BaseModel, Field, StringConstraints


NonEmptyStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class FallEventRequest(BaseModel):
    event: Literal["fall_detected"] = Field(default="fall_detected")
    elder_id: NonEmptyStr = Field(alias="elderId")
    location: NonEmptyStr
    confidence: float = Field(ge=0.0, le=1.0)
    source_event_id: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "source_event_id",
            "sourceEventId",
            "event_id",
            "eventId",
            "camera_event_id",
            "cameraEventId",
        ),
    )
    camera_id: str | None = Field(default=None, alias="cameraId")
    external_task_id: str | None = Field(
        default=None,
        validation_alias=AliasChoices("external_task_id", "externalTaskId", "health_new_task_id", "healthNewTaskId"),
    )
    callback_url: str | None = Field(default=None, alias="callbackUrl")

    model_config = {"populate_by_name": True}


class ConfirmFallTaskRequest(BaseModel):
    task: Literal["confirm_fall"] = Field(default="confirm_fall")
    elder_id: NonEmptyStr = Field(alias="elderId")
    location: NonEmptyStr
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    source_event_id: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "source_event_id",
            "sourceEventId",
            "event_id",
            "eventId",
            "camera_event_id",
            "cameraEventId",
        ),
    )
    camera_id: str | None = Field(default=None, alias="cameraId")
    external_task_id: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "external_task_id",
            "externalTaskId",
            "health_new_task_id",
            "healthNewTaskId",
            "task_id",
            "taskId",
        ),
    )
    callback_url: str | None = Field(default=None, alias="callbackUrl")
    priority: Literal["high"] = Field(default="high")

    model_config = {"populate_by_name": True}

    def to_fall_event(self) -> FallEventRequest:
        return FallEventRequest(
            event="fall_detected",
            elder_id=self.elder_id,
            location=self.location,
            confidence=self.confidence,
            source_event_id=self.source_event_id,
            camera_id=self.camera_id,
            external_task_id=self.external_task_id,
            callback_url=self.callback_url,
        )


class TargetMoveRequest(BaseModel):
    location: NonEmptyStr
    task: Literal["move_to_target"] = Field(default="move_to_target")
    priority: Literal["normal", "high"] = Field(default="normal")

    model_config = {"populate_by_name": True}


class FollowTaskRequest(BaseModel):
    target: NonEmptyStr = Field(default="elder001")


class PatrolTaskRequest(BaseModel):
    route: NonEmptyStr = Field(default="default")


class VoiceResultRequest(BaseModel):
    voice_result: NonEmptyStr = Field(alias="voiceResult")
    need_help: bool | None = Field(default=None, alias="needHelp")

    model_config = {"populate_by_name": True}


class ElderResponseRequest(BaseModel):
    response_type: Literal["SAFE", "NEED_HELP", "UNKNOWN"] = Field(
        validation_alias=AliasChoices("response_type", "responseType")
    )
    transcript: str | None = None

    model_config = {"populate_by_name": True}


class ReplayFeedbackRequest(BaseModel):
    callback_url: str | None = Field(default=None, alias="callbackUrl")

    model_config = {"populate_by_name": True}


class CancelTaskRequest(BaseModel):
    reason: NonEmptyStr = Field(default="cancelled_by_request")
