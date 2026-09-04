# Robot Video Gateway frozen runtime: 2026-09-02

This directory preserves the last locally recoverable runtime compiled on
2026-09-02 before the 2026-09-03 video watchdog changes. It is retained as
evidence and is no longer the active decoder runtime.

## Provenance

- CPython bytecode version: 3.9
- Embedded source timestamp: `2026-09-02 08:33:00`
- Embedded source size: `159700` bytes
- Runtime SHA-256: `05BAB7C9A5766B8F3B80896DF03F6E217A6785FD6166B6FFA40D8C05BA92BFCF`
- Original source file was overwritten on 2026-09-03; therefore this snapshot
  intentionally runs the exact timestamped bytecode through the retained
  Python 3.9 environment.

The snapshot package overlay is placed before the project source directory.
All other imported project files were last modified no later than 2026-09-02
for this runtime path, including `video_bridge.py`, the CLI entry point, and
the original PowerShell launcher.

## Current network configuration

The runtime behavior is frozen to 2026-09-02, while current network addresses
are supplied by the wrapper:

- Go2: `192.168.8.245`
- machine-dog computer: `192.168.8.254`
- video: `http://192.168.8.254:8093/stream.mjpg`

Do not edit or regenerate `go2_wireless_runtime.pyc`.

The retained Python 3.9 WebRTC environment did not contain PyYAML. A local
pure-Python copy of PyYAML 6.0.3 is therefore stored under `vendor/yaml`; this
does not change the frozen runtime logic and requires no network installation.

## Active compatibility mode

The exact bytecode requires aiortc 1.9 / PyAV 12.3 and cannot reliably decode
the current Go2 H.264 stream (`missing PPS` / `invalid NAL`). The active launcher
therefore uses Python 3.12 with aiortc 1.15 / PyAV 17.1 and configures the source
runtime with the 2026-09-02 recovery timing:

- first-frame / soft-recovery threshold: 6 seconds
- soft-recovery observation: 3 seconds
- reconnect cooldown: 0 seconds
