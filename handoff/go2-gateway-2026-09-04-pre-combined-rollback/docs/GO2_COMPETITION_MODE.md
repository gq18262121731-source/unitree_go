# Go2 Competition Mode

Competition Mode uses one command, one process, and exactly one Go2 WebRTC
PeerConnection. Computer B never connects to Go2; it only reads the MJPEG relay
from Computer A.

```text
Go2
  -> one WebRTC PeerConnection on Computer A
     ├── DataChannel -> phone_demo motion
     │                 + high-level pose + AudioHub speech
     └── Video Track  -> A:0.0.0.0:8093 -> Computer B
```

## Computer A

Close the Unitree App, confirm Companion is `IDLE`, keep the original remote
ready, and clear at least a 4 m × 4 m area. Then run:

```powershell
cd "E:\笨笨狗\go2_dev\go2-gateway"
.\scripts\Start-Go2CompetitionDemo.ps1
```

The launcher fixes these competition settings:

```text
RobotIp   = 192.168.8.252
VideoBind = 0.0.0.0
VideoPort = 8093
AutoDemo  = phone_demo
```

It still requires exact safety confirmations; it never silently bypasses the
exclusive-writer, App-closed, open-area, or full-demo gates:

```text
EXCLUSIVE_MOTION_WRITER
UNITREE_APP_CLOSED
OPEN_AREA_REMOTE_READY
COMPETITION_PHONE_DEMO_APPROVED
POSE_AUDIO_REAL_APPROVED
```

After WebRTC, DataChannel, SportModeState, video, and the local HTTP relay are
ready, `configs/phone_demo.yaml` starts automatically. It contains movement,
the two pose values, and the final spoken text. No competition action values
are embedded in the launcher or controller.

When the YAML sequence finishes, an explicit StopMove is sent and the console
prints:

```text
PHONE_DEMO_COMPLETE
MOTION=STOPPED
VIDEO=ACTIVE
WEBRTC=CONNECTED
```

The process remains active so Computer B keeps receiving video.

## Computer B

The Computer A address is printed as the `LAN` URL at startup. With the current
network it is expected to resemble:

```text
http://192.168.8.254:8093/stream.mjpg
```

Computer B may also use:

```text
http://<Computer-A-IP>:8093/
http://<Computer-A-IP>:8093/status
http://<Computer-A-IP>:8093/snapshot
http://<Computer-A-IP>:8093/stream.mjpg
```

TCP 8093 must be allowed by Windows Firewall on Computer A, and the Wi-Fi/AP
must permit client-to-client traffic. The launcher prints a reminder but does
not silently modify Windows Firewall policy.

## Runtime commands after automatic start

```text
STOP       StopMove only; WebRTC/video/8093 remain active
STATUS     Show connection, state, and video counters
START_DEMO Re-run only after explicit PHONE_DEMO_APPROVED confirmation
POSE_GATE  Validate the first pose independently before full competition use
AUDIO_GATE Validate Go2 speaker playback independently
EXIT       StopMove, close 8093, disconnect WebRTC, exit
```

Always use `EXIT` for normal shutdown. Do not force-kill the Runtime process.

## Frozen motion behavior

Competition Mode does not modify:

- `ScriptedMotionController`
- position/yaw closed-loop logic
- clockwise sign (`wz < 0`)
- `configs/phone_demo.yaml`
- tuned speeds, tolerances, stalls, timeouts, or 5 Hz control rate

Pose and speech are additional serial YAML actions. They do not modify the
validated translation/rotation controller. The required first-use order is
`POSE_GATE`, then `AUDIO_GATE`, then the full competition sequence.
