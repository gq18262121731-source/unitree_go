# Phase 7.2-C Continuous Velocity Refresh Implementation Report

Date: 2026-08-23

## Result

```text
Offline implementation                 PASS
Legacy move(duration) auto-stop        PRESERVED
Continuous 5 Hz velocity refresh       IMPLEMENTED
Unsafe-transition StopMove             PRESERVED
Watchdog fail-safe                     ACTIVE (0.5 s default)
Post-change real-hardware execution    NOT RUN
Real continuous follow                 CLOSED
```

## Root cause confirmed

The previous supervised path called `RobotService.move()` once per successful
cycle. That method always called `gateway.stop()` in its `finally` block, so the
actual sequence was:

```text
Move -> wait 0.10 s -> StopMove -> re-evaluate -> Move -> StopMove
```

This can repeatedly interrupt gait startup. Unitree's official SDK examples
instead refresh `SportClient.Move()` in a recurrent loop and use `StopMove()`
for the stop state.

## Implementation

### RobotService

Added `refresh_velocity(vx, vy, wz)` as a separate supervised-only path:

- validates the same velocity limits and real-hardware readiness gates;
- sends one SDK `Move` refresh;
- does not call `StopMove` after a safe refresh;
- arms and heartbeats the independent 0.5-second control watchdog;
- calls `safe_stop` if the refresh fails.

The existing `move(vx, vy, wz, duration)` behavior is unchanged and still
auto-stops in `finally`, preserving the public API and earlier single-pulse
tests.

### RealFollowExecutor

Added an explicit `continuous_velocity_refresh` mode. When enabled:

```text
safe FOLLOW decision -> refresh_velocity
unsafe/preempted decision -> safe_stop
```

Any stop still clears resume authorization. Recovery remains manual.

### Supervised live tool

The Phase 7.2-C runner now:

- uses continuous velocity refresh at no more than 5 Hz;
- supports an explicit forward-speed clamp up to 0.15 m/s;
- supports the existing yaw-rate clamp up to 0.30 rad/s;
- requires the new typed confirmation `CONTINUOUS_5HZ_REFRESH`;
- reports refresh mode separately from finite-duration motion.

An additional C2 fixed-velocity test mode is available for bounded straight
and in-place-turn gates. It still requires fresh UWB, LiDAR, risk heartbeat and
manual resume, and adds the exact confirmation `FIXED_VELOCITY_GATE`. The C1
UWB-follow cap remains five refreshes; fixed tests are hard-capped at seventeen
refreshes (about 3.2 seconds from first to last refresh).

## Safety invariants retained

```text
UWB stale              -> StopMove + clear resume
LiDAR STOP             -> StopMove + clear resume
FALL_CONFIRMED         -> StopMove + emergency latch
Manual takeover        -> StopMove + clear resume
Risk heartbeat stale   -> StopMove + clear resume
Loop stall > 0.5 s     -> watchdog StopMove
Exception/shutdown     -> safe_stop
```

## Verification

- 41 focused tests passed after the final implementation.
- Full repository regression passed.
- Ubuntu 20.04 Python compile checks passed.
- Tests prove two consecutive safe refreshes do not issue an intermediate stop.
- Tests prove the first unsafe transition issues `safe_stop`.
- Tests prove legacy `move(duration)` still auto-stops.
- No post-change real SDK motion test was executed.

## Proposed next hardware gate

Do not reuse the earlier short-slice result as acceptance evidence. Run a new,
separately approved gate:

```text
5 Hz safety evaluation
vx <= 0.15 m/s
vy = 0
|wz| <= 0.05 rad/s for the first gait-start test
5 successful refreshes (about 1 second)
one StopMove at completion or immediately on any unsafe input
```

Only after that gate produces a visible stable step and all preemption paths
remain effective should the refresh count or yaw limit be increased.
