from __future__ import annotations

import time
from dataclasses import replace
from typing import Any, Callable

from app.voice.interaction_context import InteractionContext
from app.voice.xiaokang_agent import (
    ClipAssembler,
    HealthContext,
    HealthProvider,
    MedicationProvider,
    OUTING_REQUEST_TERMS,
    WeatherCondition,
    WeatherProvider,
    XiaokangDecision,
    _normalize_text,
    _weather_value,
)


class InteractionFlowController:
    def __init__(
        self,
        *,
        health_provider: HealthProvider,
        weather_provider: WeatherProvider,
        medication_provider: MedicationProvider,
        clip_assembler: ClipAssembler | None = None,
        context: InteractionContext | None = None,
        auto_follow: bool = False,
        reply_timeout_seconds: float = 10.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.health_provider = health_provider
        self.weather_provider = weather_provider
        self.medication_provider = medication_provider
        self.clip_assembler = clip_assembler or ClipAssembler()
        self.context = context or InteractionContext()
        self.auto_follow = bool(auto_follow)
        self.reply_timeout_seconds = max(1.0, float(reply_timeout_seconds))
        self._clock = clock
        self._operator_replay_decisions: dict[str, XiaokangDecision] = {}

    def clear_pending_reply(self) -> None:
        if self.context.expected_reply == "medication_and_departure":
            self.context.outing_state = "idle"
        self.context.clear_expected_reply()

    def reset_demo(self) -> None:
        self.context.reset_demo()
        self._operator_replay_decisions.clear()

    def _operator_replay_decision(
        self,
        event: str,
    ) -> XiaokangDecision | None:
        decision = self._operator_replay_decisions.get(event)
        if decision is None:
            return None
        return replace(
            decision,
            intent=f"{decision.intent}_replay",
            action=None,
        )

    def handle_text(self, text: str) -> XiaokangDecision:
        normalized = _normalize_text(text)
        self.context.expire_expected_reply(now=self._clock())
        if not normalized:
            return self._idle_ack()
        if _is_stop_follow(normalized):
            self.context.reset_outing_reply_window()
            return XiaokangDecision(
                intent="stop_follow",
                action="stop_follow",
                clips=tuple(self.clip_assembler.stop_follow()),
                reply="好，我先不跟着您了。",
            )
        if self.context.safety_state.startswith("fall"):
            return self._handle_fall_text(normalized)
        if _is_health_weather_query(normalized):
            return self._health_weather_query()
        if _is_outing_request(normalized):
            self.context.clear_expected_reply()
            return self._handle_outing_request()
        if self.context.expected_reply == "medication_and_departure":
            return self._handle_medication_departure_reply(normalized)
        if self.context.expected_reply == "reading_reply":
            self.context.clear_expected_reply()
            return XiaokangDecision(
                intent="reading_reply",
                action=None,
                clips=tuple(self.clip_assembler.wake_ack()),
                reply="好的，我陪您待一会儿。",
            )
        return self._idle_ack()

    def handle_event(self, event: str, payload: dict[str, Any] | None = None) -> list[XiaokangDecision]:
        normalized = str(event or "").strip().upper()
        event_payload = dict(payload or {})
        if bool(event_payload.get("replay")):
            decision = self._operator_replay_decision(normalized)
            if decision is None:
                return []
            return [decision]
        if normalized == "FALL_SUSPECTED":
            self.context.reset_outing_reply_window()
            self.context.safety_state = "fall_check_1"
            self.context.demo_phase = "fall_check_1"
            self.context.fall_user_response = None
            self.context.fall_visual_recovered = False
            return [
                XiaokangDecision(
                    intent="fall_suspected",
                    action=(
                        None
                        if bool(event_payload.get("motion_already_stopped"))
                        else "stop_follow"
                    ),
                    clips=("fall.confirm",),
                    reply="我看到您可能摔倒了。您现在还好吗？",
                )
            ]
        if normalized == "FALL_RESPONSE_TIMEOUT":
            if self.context.safety_state not in {"fall_check_1", "fall_check_2"}:
                return []
            if (
                bool(event_payload.get("skip_second_prompt"))
                and self.context.safety_state == "fall_check_1"
            ):
                self.context.safety_state = "fall_check_2"
                self.context.demo_phase = "fall_check_2"
            return [self._handle_fall_timeout()]
        if normalized == "FALL_RECOVERED":
            if self.context.safety_state not in {
                "fall_check_1",
                "fall_check_2",
                "helping",
            }:
                return []
            self.context.fall_visual_recovered = True
            if bool((payload or {}).get("force_recovered")):
                self.context.fall_user_response = "ok"
            if self.context.fall_user_response == "ok":
                return [self._fall_recovered()]
            return []
        if normalized == "NORMAL_ACTIVITY_READING":
            if self.context.safety_state == "helping":
                return []
            self.context.safety_state = "normal"
            self.context.demo_phase = "wait_resume"
            self.context.clear_expected_reply()
            return []
        if normalized == "OPERATOR_OUTING_ASSESSMENT":
            replay = self._operator_replay_decision(normalized)
            if replay is not None:
                return [replay]
            self.context.clear_expected_reply()
            decision = self._operator_outing_assessment()
            self.context.skill2_done = True
            self.context.outing_state = "idle"
            self.context.demo_phase = "skill3_ready"
            self.context.clear_expected_reply()
            self._operator_replay_decisions[normalized] = decision
            return [decision]
        if normalized == "OPERATOR_MEDICATION_CHECK":
            replay = self._operator_replay_decision(normalized)
            if replay is not None:
                return [replay]
            self.context.clear_expected_reply()
            decision = self._operator_medication_check()
            self._operator_replay_decisions[normalized] = decision
            return [decision]
        if normalized == "OPERATOR_DEPART":
            replay = self._operator_replay_decision(normalized)
            if replay is not None:
                return [replay]
            decision = self._outing_start()
            self._operator_replay_decisions[normalized] = decision
            return [decision]
        return []

    def _handle_outing_request(self) -> XiaokangDecision:
        medication = self.medication_provider.get_status()
        if self.context.health_assessment_valid and self.context.medication_taken:
            self.context.departure_confirmed = True
            self.context.outing_state = "wait_start_playback"
            self.context.demo_phase = "following_start_pending"
            return XiaokangDecision(
                intent="outing_resume",
                action="start_follow" if self.auto_follow else None,
                clips=("follow.resume.safe",),
                reply="好，咱们继续走吧。",
            )
        if medication.required_today and not self.context.medication_taken:
            if not self.context.medication_reminded:
                decision = self._outing_assessment(
                    health=self._health_for_profile("outing_before_medication"),
                    medication_reminder=True,
                    action=None,
                )
                self.context.health_assessment_valid = True
                self.context.medication_reminded = True
                self.context.outing_state = "wait_medication"
                self.context.demo_phase = "skill2_wait_ack"
                self.context.set_expected_reply(
                    "medication_and_departure",
                    now=self._clock(),
                    timeout_seconds=self.reply_timeout_seconds,
                )
                return decision
            self.context.outing_state = "wait_medication_confirm"
            self.context.demo_phase = "skill3_wait_depart"
            self.context.set_expected_reply(
                "medication_and_departure",
                now=self._clock(),
                timeout_seconds=self.reply_timeout_seconds,
            )
            decision = self._outing_assessment(
                health=self._health_for_profile("outing_after_medication"),
                medication_reminder=False,
                action=None,
            )
            return XiaokangDecision(
                **{
                    **decision.__dict__,
                    "intent": "outing_medication_check",
                    "clips": tuple(decision.clips) + ("outing.medication_check",),
                    "reply": "刚才提醒您的药已经吃过了吗？",
                }
            )
        return self._outing_start()

    def _handle_medication_departure_reply(self, normalized: str) -> XiaokangDecision:
        if _is_medication_taken(normalized):
            self.context.medication_taken = True
            self.context.medication_acknowledged = True
        elif _is_soft_ack(normalized):
            self.context.medication_acknowledged = True
            self.context.outing_state = "idle"
            self.context.demo_phase = "skill3_ready"
            self.context.clear_expected_reply()
            return XiaokangDecision(
                intent="medication_reminder_ack",
                action=None,
                clips=(),
                reply="好的。",
            )
        if _is_departure_confirmed(normalized):
            self.context.departure_confirmed = True
        if self.context.medication_taken and self.context.departure_confirmed:
            self.context.clear_expected_reply()
            return self._outing_start()
        self.context.outing_state = "wait_medication_confirm"
        self.context.demo_phase = "skill3_wait_depart"
        self.context.set_expected_reply(
            "medication_and_departure",
            now=self._clock(),
            timeout_seconds=self.reply_timeout_seconds,
        )
        return XiaokangDecision(
            intent="outing_medication_check",
            action=None,
            clips=("outing.medication_check",),
            reply="刚才提醒您的药已经吃过了吗？",
        )

    def _operator_medication_check(self) -> XiaokangDecision:
        health = self._health_for_profile("outing_after_medication")
        weather = self.weather_provider.get_weather()
        condition = _weather_value(weather.condition)
        self.context.outing_state = "wait_medication_confirm"
        self.context.demo_phase = "skill3_wait_depart"
        self.context.set_expected_reply(
            "medication_and_departure",
            now=self._clock(),
            timeout_seconds=self.reply_timeout_seconds,
        )
        decision = XiaokangDecision(
            intent="outing_medication_check",
            allowed=True,
            health_status=health.status,
            weather=None if condition is WeatherCondition.UNKNOWN else condition.value,
            temperature=weather.temperature,
            action=None,
            reply="刚才提醒您的药已经吃过了吗？",
        )
        return XiaokangDecision(
            **{
                **decision.__dict__,
                "clips": tuple(
                    self.clip_assembler.medication_check(
                        weather=decision.weather,
                        temperature=decision.temperature,
                    )
                ),
            }
        )

    def _operator_outing_assessment(self) -> XiaokangDecision:
        decision = self._outing_assessment(
            health=self._health_for_profile("outing_before_medication"),
            medication_reminder=True,
            action=None,
        )
        self.context.health_assessment_valid = True
        self.context.medication_reminded = True
        return decision

    def _outing_start(self) -> XiaokangDecision:
        self.context.health_assessment_valid = True
        self.context.medication_taken = True
        self.context.departure_confirmed = True
        self.context.outing_state = "wait_start_playback"
        self.context.demo_phase = "skill3_first_start_pending"
        return XiaokangDecision(
            intent="outing_start",
            allowed=True,
            health_status="good",
            action="start_follow" if self.auto_follow else None,
            clips=("outing.start",),
            reply="好，咱们出发吧，您慢慢走，我跟着您。",
        )

    def _outing_assessment(
        self,
        *,
        health: HealthContext,
        medication_reminder: bool,
        action: str | None,
    ) -> XiaokangDecision:
        weather = self.weather_provider.get_weather()
        condition = _weather_value(weather.condition)
        decision = XiaokangDecision(
            intent="outing_request",
            allowed=True,
            health_status=health.status,
            heart_rate=health.heart_rate,
            spo2=health.spo2,
            body_temperature=health.temperature,
            weather=None if condition is WeatherCondition.UNKNOWN else condition.value,
            temperature=weather.temperature,
            medication_reminder=medication_reminder,
            action=action,
            reply="可以出去走走，我陪着您。",
        )
        return XiaokangDecision(
            **{
                **decision.__dict__,
                "clips": tuple(self.clip_assembler.outing_allow(decision)),
            }
        )

    def _health_weather_query(self) -> XiaokangDecision:
        health = self._health_for_profile("health_query")
        decision = self._outing_assessment(
            health=health,
            medication_reminder=False,
            action=None,
        )
        return XiaokangDecision(
            **{
                **decision.__dict__,
                "intent": "health_weather_query",
                "reply": "我来帮您看一下身体和天气。",
            }
        )

    def _handle_fall_text(self, normalized: str) -> XiaokangDecision:
        if _is_user_ok(normalized):
            self.context.fall_user_response = "ok"
            if self.context.fall_visual_recovered:
                return self._fall_recovered()
            return XiaokangDecision(
                intent="fall_user_ok_wait_visual",
                action=None,
                clips=(),
                reply="好的，我再确认一下您的状态。",
            )
        return self._idle_ack()

    def _handle_fall_timeout(self) -> XiaokangDecision:
        if self.context.safety_state == "fall_check_1":
            self.context.safety_state = "fall_check_2"
            self.context.demo_phase = "fall_check_2"
            return XiaokangDecision(
                intent="fall_confirm_second",
                action=None,
                clips=("fall.confirm.second",),
                reply="您能听到我说话吗？如果可以，请回答我。",
            )
        self.context.safety_state = "helping"
        self.context.demo_phase = "helping"
        return XiaokangDecision(
            intent="fall_help_broadcast",
            action=None,
            clips=("fall.alert.sound", "fall.help.broadcast"),
            reply="已经通知家属，请附近的人过来帮忙查看。",
        )

    def _fall_recovered(self) -> XiaokangDecision:
        self.context.safety_state = "normal"
        self.context.demo_phase = "wait_reading"
        self.context.fall_user_response = None
        self.context.fall_visual_recovered = False
        self.context.outing_state = "idle"
        self.context.clear_expected_reply()
        return XiaokangDecision(
            intent="fall_recovered",
            action=None,
            clips=("fall.recovered",),
            reply="好的，看到您现在已经恢复了，这次情况我已经记录了。",
        )

    def _health_for_profile(self, profile: str) -> HealthContext:
        provider = self.health_provider
        get_profile = getattr(provider, "get_profile", None)
        if callable(get_profile):
            return get_profile(profile)
        return provider.get_current_health()

    def _idle_ack(self) -> XiaokangDecision:
        return XiaokangDecision(
            intent="unknown",
            action=None,
            clips=(),
            reply="",
        )


def _is_outing_request(text: str) -> bool:
    return any(term in text for term in OUTING_REQUEST_TERMS)


def _is_stop_follow(text: str) -> bool:
    return any(term in text for term in ("停一下", "不用跟着", "停止伴随", "别跟着", "不要跟着"))


def _is_health_weather_query(text: str) -> bool:
    return any(term in text for term in ("身体", "健康", "心率", "血氧", "天气")) and not _is_outing_request(text)


def _is_soft_ack(text: str) -> bool:
    return text in {"好的", "好", "知道了", "行", "可以"}


def _is_medication_taken(text: str) -> bool:
    return any(term in text for term in ("吃过了", "吃了", "服过了", "服药了", "已经吃", "已经服"))


def _is_departure_confirmed(text: str) -> bool:
    return any(term in text for term in ("现在出发", "出发", "走吧", "可以走了", "开始走"))


def _is_user_ok(text: str) -> bool:
    return any(term in text for term in ("我没事", "没事", "还好", "不用帮忙", "没有摔"))
