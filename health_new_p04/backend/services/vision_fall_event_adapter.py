from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping

from backend.models.companion_risk_model import CompanionRiskEvent, CompanionRiskEventType


_QWEN_REVIEW_FIELDS = (
    "analysis_status",
    "review_result",
    "confidence",
    "posture",
    "movement_level",
    "attempt_to_stand",
    "persistent_ground_state",
    "possible_help_gesture",
    "visibility",
    "occlusion",
    "scene_risks",
    "summary",
    "review_points",
    "evidence",
    "uncertainties",
    "community_advice",
    "model_role",
    "model_name",
    "latency_ms",
    "provider",
)
_REVIEW_RESULT_MAP = {
    "likely_fall": "confirmed_fall",
    "likely_false_positive": "likely_false_positive",
    "uncertain": "uncertain",
}


class VisionFallEventAdapter:
    """Normalize Vision/PFV2 payloads without exposing model details to Companion."""

    def normalize_event(self, event: Mapping[str, Any]) -> dict[str, Any]:
        normalized = dict(event)
        metadata = dict(normalized.get("metadata") or {}) if isinstance(normalized.get("metadata"), Mapping) else {}
        qwen_review = self.extract_qwen_review(normalized)
        if qwen_review is not None:
            metadata["qwen_review"] = qwen_review
            normalized["qwen_review"] = qwen_review
            normalized["multimodal_review"] = self.multimodal_review(qwen_review)
            metadata["multimodal_review"] = normalized["multimodal_review"]
        normalized["metadata"] = metadata
        normalized["companion_event_type"] = self.resolve_event_type(normalized).value
        normalized.setdefault("schema_version", "companion_fall_event.v1")
        normalized.setdefault("event_id", normalized.get("incident_id"))
        return normalized

    def extract_qwen_review(self, event: Mapping[str, Any]) -> dict[str, Any] | None:
        metadata = event.get("metadata") if isinstance(event.get("metadata"), Mapping) else {}
        event_metadata = metadata.get("event") if isinstance(metadata.get("event"), Mapping) else {}
        candidate = metadata.get("qwen_review")
        if not isinstance(candidate, Mapping):
            candidate = event.get("qwen_advisory")
        if not isinstance(candidate, Mapping):
            candidate = event_metadata.get("qwen_advisory")
        if not isinstance(candidate, Mapping):
            return None
        return {field: candidate[field] for field in _QWEN_REVIEW_FIELDS if field in candidate}

    @staticmethod
    def multimodal_review(qwen_review: Mapping[str, Any]) -> dict[str, Any]:
        review_result = str(qwen_review.get("review_result") or "uncertain").strip()
        return {
            "status": qwen_review.get("analysis_status"),
            "judgement": _REVIEW_RESULT_MAP.get(review_result, "uncertain"),
            "confidence": qwen_review.get("confidence"),
            "reason": qwen_review.get("summary"),
            "recommended_action": qwen_review.get("community_advice"),
            "provider": qwen_review.get("provider"),
            "model_name": qwen_review.get("model_name"),
            "latency_ms": qwen_review.get("latency_ms"),
        }

    def resolve_event_type(self, event: Mapping[str, Any]) -> CompanionRiskEventType:
        raw_event_type = str(event.get("event_type") or "").strip()
        if raw_event_type in CompanionRiskEventType._value2member_map_:
            return CompanionRiskEventType(raw_event_type)

        review = self.extract_qwen_review(event)
        review_result = str((review or {}).get("review_result") or "").strip()
        if review_result == "likely_fall":
            return CompanionRiskEventType.FALL_CONFIRMED
        if review_result == "likely_false_positive":
            return CompanionRiskEventType.FALL_DISMISSED
        if review_result == "uncertain":
            return CompanionRiskEventType.FALL_SUSPECTED

        lowered = raw_event_type.lower()
        state = str(event.get("state") or event.get("status") or "").strip().lower()
        if lowered in {"fall_suspected", "fall_candidate"} or state in {"fall_suspected", "fall_candidate"}:
            return CompanionRiskEventType.FALL_SUSPECTED
        if lowered in {"fall_confirmed", "fall_detected", "fall"} or state in {"confirmed_fall", "fallen"}:
            return CompanionRiskEventType.FALL_CONFIRMED
        return CompanionRiskEventType.FALL_SUSPECTED

    def to_companion_risk_event(self, event: Mapping[str, Any]) -> CompanionRiskEvent:
        normalized = self.normalize_event(event)
        metadata = normalized.get("metadata") if isinstance(normalized.get("metadata"), Mapping) else {}
        review = normalized.get("qwen_review") if isinstance(normalized.get("qwen_review"), Mapping) else {}
        confidence = review.get("confidence")
        if confidence is None:
            confidence = normalized.get("confidence")
        if confidence is None:
            confidence = normalized.get("fall_score") if normalized.get("fall_score") is not None else normalized.get("fall_prob")
        timestamp = normalized.get("timestamp") or datetime.now(timezone.utc)
        return CompanionRiskEvent(
            event_type=self.resolve_event_type(normalized),
            incident_id=str(normalized.get("incident_id") or normalized.get("event_id") or "").strip(),
            timestamp=timestamp,
            confidence=confidence,
            source=str(normalized.get("source") or "vision_service"),
            camera_id=str(normalized.get("camera_id") or "").strip() or None,
            elder_id=str(normalized.get("elder_id") or metadata.get("elder_id") or "").strip() or None,
            metadata={"vision_schema_version": normalized.get("schema_version")},
        )
