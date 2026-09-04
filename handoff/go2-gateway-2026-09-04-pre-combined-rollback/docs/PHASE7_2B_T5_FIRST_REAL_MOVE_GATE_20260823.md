# Phase 7.2-B T5 First Real Move Gate — 2026-08-23

## Result

```text
T5 first real forward pulse   PASS
Continuous follow            NOT STARTED
Default real-motion switch   CLOSED
```

## Preconditions

- The operator confirmed that the test area was clear.
- Go2 was stable and facing an open area.
- Unitree LeadFollow remained disabled.
- A second safety operator held the original remote controller.
- A real `StopMove` preflight completed with exit code 0 before the pulse.

## Executed pulse

```text
vx       = +0.05 m/s
vy       = 0
wz       = 0
duration = 0.20 s
```

Theoretical displacement was approximately `0.01 m`. The one-shot tool returned
`execution_result=sent` and SDK `code=0`. The command path issued its bounded
stop/finalization sequence and exited; no continuous follow process was started.

##现场观察

The operator confirmed all required observations:

- actual direction was forward and correct;
- Go2 stopped immediately after the pulse;
- no lateral motion, sudden acceleration, or posture anomaly occurred;
- the original remote controller remained able to take over.

## Postconditions

```text
Non-zero Move calls       1
Residual motion processes 0
PHASE7 default            false
Go2 network               reachable
```

T6 rotation remains closed until separately authorized. This result does not
approve continuous UWB following, obstacle-stop motion tests, dropout-stop motion
tests, or fall-event motion tests.
