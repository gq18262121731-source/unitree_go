# Go2 Voice Software Stage Report - 2026-09-09

## Current Conclusion

The B-machine software chain is now implemented as a local-first demo path:

```text
Go2 built-in microphone
-> WebRTC audio track
-> 48 kHz stereo PCM probe
-> 16 kHz mono PCM normalization
-> local FunASR / paraformer-zh-streaming
-> Xiaokang wake word
-> local session manager
-> standard speech JSON
-> MockTransport / command dispatcher
-> fixed clip playback
-> optional start_follow after clip_done
```

The current default demo path does not depend on real MQTT, dynamic runtime TTS,
or direct LLM control of robot motion.

## Methods Used

- WebRTC audio reception reuses the existing Go2 connection. The decoded audio
  frame is obtained from the WebRTC audio track and converted with
  `frame.to_ndarray()`.
- Go2 audio is kept separate from PC microphone input. `--audio-source local`
  remains only as a development fallback; `--audio-source go2` is the target
  hardware path.
- Go2 microphone frames continue to feed the existing WAV recording buffer, and
  a separate ASR queue feeds FunASR so WebRTC callbacks are not blocked.
- Go2 audio format is normalized before ASR. The measured Go2 first-frame format
  was 48 kHz, 2 channels, int16, 20 ms frames, then normalized to 16 kHz mono
  int16 PCM for FunASR.
- Startup confirmation prompts were removed from the runtime start path, while
  STOP, emergency stop, state gates, idempotent START, and shutdown stop fallback
  remain.
- Dialogue flow is handled by `InteractionContext` and
  `InteractionFlowController`, instead of putting all scenario state inside the
  agent service.
- The fixed voice path uses clip IDs plus local WAV files. Dynamic data is
  converted by code, not by the agent: heart rate still uses complete number
  clips such as `num.76`, while weather/body temperature now uses natural
  temperature clips such as `temperature.value.17` and
  `temperature.value.36_5` instead of `num.* + unit.celsius`.
- Emergency help broadcast keeps normal Cherry voice resources untouched. Only
  `fall.alert.sound` and `fall.help.broadcast` use the emergency playback
  profile: alarm first, short pause, longer playback timeout, volume gain, and
  peak limiting.

## Implemented Demo Flow

- Health/weather query speaks current health and weather data and does not start
  following.
- First outing request performs health/weather assessment and reminds medication;
  it does not start following.
- A soft acknowledgement such as `好的` does not mark medication as taken.
- A later medication/departure reply such as `吃过了，现在出发` marks medication
  taken, plays `outing.start`, then starts following only after `clip_done`.
- Stop intent sends `stop_follow` immediately, then plays the stop clip.
- Later outing requests reuse valid health/medication context and play
  `follow.resume.safe` instead of repeating the long report.
- `FALL_SUSPECTED` clears pending start actions, sends an immediate stop, and
  plays the first fall confirmation.
- Fall response timeout escalates from `fall.confirm.second` to the help
  broadcast clips.
- Fall recovery requires both user OK text and `FALL_RECOVERED`; following is
  not resumed automatically.
- `NORMAL_ACTIVITY_READING` plays the normal-activity/read-book branch, while a
  new Xiaokang outing command cancels that reply window.

## Prepared Voice Resources

Current smoke check:

- Dynamic clip definitions: 315
- Existing WAV files: 331
- Missing dynamic clips: 0
- WAV format: 1 channel, 24 kHz, 16 bit
- Number range covered: `num.minus10` through `num.130`
- Natural weather/body temperature clips covered:
  `temperature.value.-10` through `temperature.value.45`, plus body-temperature
  decimals `temperature.value.35_1` through `temperature.value.41_9`
- Blood oxygen clip IDs are now defined as `health.spo2.80` through
  `health.spo2.100`, with `health.spo2.prefix` as fallback support. These WAVs
  have been generated with Cherry.
- Weather clips covered: sunny, cloudy, overcast, rain, snow
- Scenario clips covered: medication check, outing start, follow resume/stop,
  fall confirmation/escalation/recovery, reading prompt
- `fall_help_broadcast.wav` was regenerated separately as a Cherry emergency
  broadcast clip at speed 1.08. Runtime emergency gain produced a checked peak
  of 22731, below the 32700 limiter threshold.
- Blood-oxygen resource generation was rerun after the original TTS API account
  was restored. `health_spo2_prefix.wav` and `health_spo2_80.wav` through
  `health_spo2_100.wav` were written successfully.

The reusable checker is:

```powershell
python .\tools\smoke_voice_clips.py --medication-reminder --play
```

Latest live smoke output at 2026-09-09 11:33:

- Weather API: Beijing, sunny, 23 C, no precipitation
- Generated WAV:
  `artifacts\voice_smoke\beijing_outing_live_smoke.wav`
- Clip path:
  `outing.allow.health_good -> health.hr.prefix -> num.76 -> unit.bpm -> health.spo2.98 -> health.temperature.prefix -> temperature.value.36_5 -> weather.condition.sunny -> weather.temperature.prefix -> temperature.value.23 -> outing.allow.suffix`
- Duration: 15.6 seconds
- Playback attempt: success
- Report:
  `artifacts\voice_smoke\beijing_outing_live_report.json`

## Key Commands

Start the Go2 voice ASR runtime:

```powershell
cd "E:\unitree\unitree_go\go2_dev\go2-gateway"
conda activate torchgpu
.\scripts\Start-Go2VoiceAsr.ps1
```

Start with ASR/VAD debug logs:

```powershell
.\scripts\Start-Go2VoiceAsr.ps1 --voice-debug
```

Allow the voice flow to start following after `clip_done`:

```powershell
$env:XIAOKANG_AUTO_FOLLOW = "true"
.\scripts\Start-Go2VoiceAsr.ps1
```

Check voice resources, Beijing weather, clip composition, and local playback:

```powershell
python .\tools\smoke_voice_clips.py --medication-reminder --play
```

Rebuild all missing dynamic clips without overwriting existing files:

```powershell
python .\tools\build_voice_presets.py --health-url http://127.0.0.1:8765 --dynamic-clips --skip-existing --voice Cherry --output-dir .\data\voice\presets\current
```

Regenerate only the emergency help broadcast clip:

```powershell
python .\tools\build_voice_presets.py --health-url http://127.0.0.1:8765 --dynamic-only fall.help.broadcast --voice Cherry --speed 1.08 --output-dir .\data\voice\presets\current
```

## On-Site Rescue Hotkeys

The rescue hotkeys are intentionally small. They are for recovering the demo
when voice or vision misses a critical event, not for mapping every feature.

```text
F1   start or resume following
F2   stop following motion
F5   trigger the full suspected-fall flow
F6   trigger fall recovery
F7   trigger normal sitting/reading flow
F9   reset demo context
F10  toggle Xiaokang business listener
F12  force motion stop only
```

Fall-scene voice-only rescue clips are also available:

```text
Ctrl+F5  play the first fall prompt
Ctrl+F6  play the second fall prompt
Ctrl+F7  play the alarm and help broadcast
```

`STOP`, `F2`, and `F12` are motion stops. They must not shut down the WebRTC
video stream, WebRTC audio track, voice listener, or visual output path. Only
`EXIT` requests runtime shutdown.

`F10` is a business mute switch, not an audio-device switch. While paused, the
Go2 microphone, WebRTC audio track, ASR bridge, and loaded FunASR model stay
online, but Xiaokang wake/session/safety-reply processing ignores transcripts.
Pausing clears the pending reply window and ends the current unfinished voice
session with `listener_paused`. Resuming returns to `WAKE_GUARD`, so the next
business command must wake Xiaokang again.

Run software tests:

```powershell
python -m pytest -q
```

## Verification Evidence

- `python -m py_compile tools\smoke_voice_clips.py app\voice\clip_composer.py app\voice\xiaokang_agent.py`
- `python -m pytest tests\test_smoke_voice_clips.py tests\test_clip_composer.py tests\test_build_voice_presets.py -q`
- `python tools\smoke_voice_clips.py --medication-reminder --play`
- `python tools\smoke_voice_clips.py --preset-dir .\data\voice\presets\current --output-wav .\artifacts\voice_smoke\beijing_outing_live_smoke.wav --report .\artifacts\voice_smoke\beijing_outing_live_report.json --heart-rate 76 --play`
- `python -m pytest -q`
- `python -m pytest tests\test_wireless_competition_mode.py -q`

All commands above passed in the current workspace.

## Remaining Non-Software / Hardware Acceptance

Software-side pieces are in place, but these items still require live hardware
acceptance:

- Full Go2 runtime start with the robot connected and AES key available.
- Xiaokang wake word recognition in the live room environment.
- End-to-end demo rehearsal with real Go2 speaker playback and `clip_done`.
- Real follow start/stop motion confirmation after spoken commands.
- Fall/reading event rehearsal using the operator terminal or external event
  source.
