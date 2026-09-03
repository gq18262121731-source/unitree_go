# Companion Risk Event Integration P0-4

## Scope and authority

This phase aligns the existing Vision binary candidate, Qwen review, health_new alarm, and Companion safety contracts. It does not change either model, the frontend, UWB follow, navigation parameters, or a real robot executor.

```text
Vision binary model -> candidate authority
Qwen               -> secondary review
health_new          -> business event authority
Companion           -> deterministic motion safety authority
```

## Ports and endpoint

- health_new backend: `127.0.0.1:8000`
- local Vision Service: `127.0.0.1:8011`
- Vision push: `POST /api/v1/video-bridge/fall-events`
- Companion risk input: `POST /api/v1/robot/companion/risk-events`
- manual resume: `POST /api/v1/robot/companion/resume`

`8090` remains a possible historical robot-gateway port. It is not the formal Vision-to-health_new target.

## Vision event contract

The formal Qwen field is `metadata.qwen_review`. The receiver still accepts top-level `qwen_advisory` and `metadata.event.qwen_advisory`, then converts either into `qwen_review`.

Qwen machine values are preserved without applying a Chinese-only filter:

- `likely_fall` -> `FALL_CONFIRMED` and UI judgement `confirmed_fall`
- `likely_false_positive` -> `FALL_DISMISSED` and UI judgement `likely_false_positive`
- `uncertain` -> `FALL_SUSPECTED` and UI judgement `uncertain`

Natural-language presentation maps directly from `confidence`, `summary`, and `community_advice`. The old `advisory_confidence`, `scene_summary`, and `care_advice` names are compatibility-only and are not used on the main path.

Qwen review data is stored at `data/fall_events/qwen_review/<incident_id>.json`. A keyframe package may be `ready` or `degraded`; a missing offset does not invalidate an otherwise completed Qwen review.

The PFV2 sender publishes twice when needed, always with the same `incident_id`:

1. Binary multi-frame candidate -> `FALL_SUSPECTED` immediately.
2. Qwen completion -> `FALL_CONFIRMED`, `FALL_DISMISSED`, or `FALL_SUSPECTED`.

The first transition does not wait for keyframe collection or Qwen latency. The
Vision sender keeps `MAIN_SYSTEM_REPORT_DRY_RUN=true` in the provided local
configuration and refuses a real POST when its alert token/header is empty.

## Injury rule

An event becomes `FALL_INJURY_RISK` only when `injury.suspected == true`. A missing, null, empty, or `suspected=false` injury object remains `FALL_DETECTED`. Vision does not manufacture injury evidence.

## Companion safety state machine

```text
FOLLOWING
  -> FALL_SUSPECTED -> PAUSED_BY_FALL (StopMove decision immediately)
  -> FALL_CONFIRMED -> MONITORING (incident locked)

PAUSED_BY_FALL
  -> FALL_CONFIRMED -> MONITORING
  -> FALL_DISMISSED -> WAIT_RESUME
  -> uncertain      -> PAUSED_BY_FALL

MONITORING
  -> RECOVERY_CONFIRMED -> WAIT_RESUME

WAIT_RESUME
  -> manual POST /robot/companion/resume -> FOLLOWING
```

`NON_FALL`, `FALL_DISMISSED`, and `RECOVERY_CONFIRMED` never auto-resume motion. A confirmed incident cannot be dismissed by a later Qwen or non-fall result. A mismatched recovery incident is rejected.

The phase uses only `disabled` or `mock` motion executors. The mock records `STOP_MOVE` and `RESUME_COMPANION` decisions but sends no physical command. The legacy fall-confirmation movement task is blocked with `COMPANION_RISK_LOCK_ACTIVE`; this prevents its automatic approach/navigation path from competing for control.

## Authentication gate

Development mode retains configured-host compatibility. When `VISION_BRIDGE_PRODUCTION_MODE=true`, a non-empty configured push token and an exact `X-Vision-Service-Token` match are mandatory; source-IP matching alone is rejected.

## Compatibility and client impact

- Existing lowercase `fall_confirmed` pushes remain accepted.
- Existing `qwen_advisory` inputs remain readable.
- The fall-event response adds `incident_id`, normalized `event_type`, `deduplicated`, `qwen_review_saved`, and `multimodal_review`.
- No frontend change is required: health_new supplies the compact `multimodal_review` view.
- Duplicate delivery of the same incident transition returns the original alarm rather than creating another alarm. A later transition for the same incident is not suppressed.

## Gate order

1. Run fixture/unit tests with disabled or mock motion.
2. POST a real Vision payload manually while Vision reporting remains dry-run.
3. Configure equal non-empty tokens and enable production bridge mode.
4. Only after HTTP, persistence, alarm, WebSocket, and Companion checks pass, enable real Vision reporting.
5. Real robot testing remains a separate final gate.

The formal schemas are:

- `docs/contracts/vision_fall_event_v1.schema.json`
- `docs/contracts/companion_risk_event_v1.schema.json`

## Acceptance result

| Gate | Result | Evidence |
| --- | --- | --- |
| Vision payload alignment | PASS | PFV2 sender fixture covers candidate and all Qwen transitions |
| Qwen review persistence | PASS | receiver writes `qwen_review/<incident_id>.json` before event dedupe |
| Qwen enum normalization | PASS | all three machine enums remain unchanged and map deterministically |
| Multimodal review mapping | PASS | confidence/summary/community_advice fixture assertions |
| Injury classification | PASS | four-way null/empty/false/true tests |
| Port 8000 contract | PASS | health_new `port=8000`; Vision runtime target `8011` |
| Token protection | PASS | dry-run empty token allowed; production missing/wrong rejected; match accepted |
| Risk event normalization | PASS | `VisionFallEventAdapter` fixture assertions |
| Incident idempotency | PASS | exact transition dedupe plus suspected-to-confirmed progression tests |
| Fall motion preemption | PASS | mock executor records `STOP_MOVE` on `FALL_SUSPECTED` |
| Manual resume only | PASS | dismissed/recovery remain locked until resume endpoint |
| Old task motion conflict | BLOCKED | legacy approach/navigation receives `COMPANION_RISK_LOCK_ACTIVE` |
| Vision -> health_new HTTP | PASS | FastAPI fixture covers degraded keyframes, persistence result, alarm, and auth |
| health_new -> go2-gateway Risk | PASS (contract) | mock HTTP session verifies exact risk endpoint and payload; runtime executor remains disabled/mock |

No live Vision reporting and no real robot command were enabled. A connected
go2-gateway process and physical motion remain later gates by design.
