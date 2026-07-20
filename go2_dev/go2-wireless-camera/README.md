# Go2 Wireless Camera Verification

Independent verification workspace for wireless Go2 front-camera capture and simulated upload.

Subprojects:

```text
collector/      read-only Go2 camera collector, local snapshot/MJPEG, simulated uploader
mock_receiver/  local upload target and preview service
```

Safety boundaries:

```text
No robot motion API calls.
No RTSP, MediaMTX, or WebRTC.
No modification to camera_79.
No modification to selection-contest-dev.
No assumption that wireless IP equals wired IP.
```

Current observed network context:

```text
Windows WLAN: 192.168.8.253
WSL wireless-side interface: eth1 / 192.168.8.253
Wired Go2 interface remains separate: eth0 / 192.168.123.222
Go2 wireless IP: not yet confirmed
```
