# Go2 Xiaokang Demo Handoff - Scenes 2 and 3

## Scope

This handoff describes the current competition demo path for Xiaokang voice
interaction on Go2:

```text
Go2 microphone
-> WebRTC audio
-> local FunASR
-> Xiaokang wake/session
-> local scenario flow
-> fixed voice clips
-> clip_done
-> Go2 follow control
```

The key rule for the outing scenario is:

```text
The robot must not start following after an outing suggestion.
It may start only after the elder explicitly confirms medication and departure,
and only after outing.start playback has finished.
```

## Scene 2: Health, Medication, Outing Confirmation

### First Outing Request

Elder:

```text
小康，陪我出去走走。
```

System flow:

```text
WAKE_GUARD
-> outing_request
-> read health data
-> read Beijing weather
-> decide outing is allowed
-> medication_taken = false
```

The first demo health profile may use:

```text
heart_rate = 78
spo2 = 98
body_temperature = 36.6
weather = Beijing overcast, 17 C
```

Xiaokang should report health and weather, then remind medication. A soft reply
such as `好的` only means the elder heard the reminder:

```text
medication_acknowledged = true
medication_taken = false
departure_confirmed = false
RobotState = IDLE
```

It must not create a pending follow action.

### Second Outing Request

When the elder returns and says:

```text
小康，陪我出去走走。
```

the scenario state is:

```text
medication_reminded = true
medication_taken = false
```

The system performs a second lightweight health/weather assessment so the demo
shows continuous monitoring. The second health profile may use:

```text
heart_rate = 77
spo2 = 98
body_temperature = 36.5
```

Then Xiaokang asks:

```text
刚才提醒您的药已经吃过了吗？如果准备好了，也可以告诉我现在出发。
```

This opens a short reply window. The elder does not need to say Xiaokang again.

### Start Following

When the elder says:

```text
吃过了，现在出发。
```

both facts must be parsed:

```text
medication_taken = true
departure_confirmed = true
```

The required execution order is:

```text
play outing.start
-> wait for clip_done
-> verify playback success
-> start_follow()
-> RobotState = FOLLOWING
```

The robot must not move while the departure voice is still playing.

### Stop During Following

Elder:

```text
小康，停一下。
```

Required order:

```text
stop_follow()
-> RobotState = IDLE
-> play follow.stop
```

Stopping follow motion must not close WebRTC video, WebRTC audio, FunASR, or the
Xiaokang listener.

## Scene 3: Follow, Fall, Recovery, Reading

### Resume Following

If health assessment is still valid and medication is already confirmed, a later
outing request is treated as a resume request. Xiaokang plays
`follow.resume.safe`, waits for `clip_done`, then starts follow.

### Fall Suspected

Fall events must share the same handler whether they come from vision/MQTT or
the F5 rescue key:

```text
FALL_SUSPECTED
```

Required behavior:

```text
if RobotState == FOLLOWING:
    stop_follow()

SafetyState = FALL_CHECK_1
RobotState = IDLE or MANUAL
VoiceState = SAFETY_LISTENING
VideoState = STREAMING
```

Only autonomous follow motion stops. Video, audio, ASR, and runtime stay online.

### Fall Voice Check

First prompt:

```text
fall.confirm
我看到您可能摔倒了。您现在还好吗？
```

If no valid response arrives, second prompt:

```text
fall.confirm.second
您能听到我说话吗？如果可以，请回答我。
```

If there is still no response:

```text
SafetyState = HELPING
fall.alert.sound
short pause
fall.help.broadcast
```

`fall.help.broadcast` uses the emergency playback profile only. Normal Cherry
companion clips stay unchanged.

### Recovery

Recovery requires both:

```text
elder voice: 我没事
vision or F6: FALL_RECOVERED
```

Then Xiaokang plays `fall.recovered`, records the incident, returns to normal,
and does not resume following automatically.

### Reading

The normal low-posture reading event is:

```text
NORMAL_ACTIVITY_READING
```

It may come from vision/MQTT or F7. Xiaokang plays:

```text
fall.normal_activity
reading.ask_book
```

If the elder ignores this small-talk window and says:

```text
小康，陪我走吧。
```

the reading reply window is cancelled, the request is treated as a new follow
request, and the system may resume follow after `follow.resume.safe` playback
finishes.

## On-Site Rescue Keys

```text
F1   start/resume following
F2   stop following motion
F5   trigger suspected-fall flow
F6   trigger fall recovered
F7   trigger normal reading flow
F9   reset DemoContext
F10  toggle Xiaokang business listener
F12  force motion stop only
```

`F2` and `F12` are motion stops, not runtime shutdown. `F10` is a business mute
switch, not a microphone or model unload switch.

## Hard Constraints

1. STOP, fall handling, F2, and F12 must not close the video path.
2. Voice, video, and motion are independent states.
3. Unconfirmed medication must block follow start.
4. `好的` must not mean medication has been taken.
5. Follow starts only after explicit departure confirmation.
6. `outing.start` must finish before movement starts.
7. Fall suspected stops autonomous follow motion while keeping video and voice.
8. Fall recovery must not auto-resume following.
9. Reading is a normal activity, not a fall.
10. F12 is motion emergency stop, not runtime shutdown.
11. F9 resets demo context only.
12. Future vision/MQTT events must use the same handlers as F5/F6/F7.

## Current Implementation Status

Implemented:

- Go2 microphone to local FunASR to Xiaokang session flow.
- Natural temperature clips such as `temperature.value.17` and
  `temperature.value.36_5`.
- Emergency-only playback mode for `fall.alert.sound` and
  `fall.help.broadcast`.
- Scene 2 start-after-clip-done guard.
- Scene 3 fall/recovery/reading event flow.
- F1/F2/F5/F6/F7/F9/F10/F12 rescue keys.

Newly aligned with this handoff:

- Blood oxygen can now be represented in the decision and clip assembler.
- The second outing request after a soft medication acknowledgement performs a
  lightweight health/weather reassessment before asking for medication status.

Pending resource item:

- `health.spo2.prefix` and `health.spo2.80` through `health.spo2.100` still need
  real WAV generation. The TTS backend currently rejects synthesis with an
  account `overdue-payment` error, so these files were not generated in this
  pass.
