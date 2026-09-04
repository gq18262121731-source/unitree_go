from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any, Optional, Protocol

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse

from app.webrtc.go2_wireless_runtime import Go2WirelessRuntime


# Keep the legacy identifier on /status so existing launchers and diagnostics
# can recognize the service while new consumers use the public gateway API.
SERVICE_ID = "go2-wireless-camera"
GATEWAY_ID = "robot-video-gateway"
RUNTIME_ID = "go2-wireless-runtime"
API_VERSION = "1"
SERVICE_VERSION = "1.2.0"


class WirelessCompanionControlError(RuntimeError):
    def __init__(self, code: str, message: str, status_code: int = 409) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


class WirelessCompanionControl(Protocol):
    def companion_status(self) -> dict[str, object]: ...

    def start_companion(self) -> dict[str, object]: ...

    def stop_companion(self) -> dict[str, object]: ...

    def resume_companion(self) -> dict[str, object]: ...

    def apply_voice_intent(self, intent_value: str) -> dict[str, object]: ...

    def ingest_risk_event(self, payload: dict[str, object]) -> dict[str, object]: ...

    def record_no_response(self) -> dict[str, object]: ...

    def manual_key(self, key: str) -> dict[str, object]: ...

    def release_manual(self) -> dict[str, object]: ...

    def reset_demo(self) -> dict[str, object]: ...

    def robot_status(self) -> dict[str, object]: ...


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat()


def create_video_bridge(
    runtime: Go2WirelessRuntime,
    *,
    follow_target_forwarder: Any | None = None,
    companion_control: WirelessCompanionControl | None = None,
    robot_id: str = "go2-01",
) -> FastAPI:
    """Create the stable HTTP/MJPEG gateway over the shared Wireless Runtime.

    Unitree/WebRTC details stay behind this boundary.  Consumers use the
    versioned discovery/status endpoints and ``/stream.mjpg`` only.
    """

    app = FastAPI(title="Go2 Unified Wireless Runtime", version=SERVICE_VERSION)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Accept", "Content-Type"],
        max_age=600,
    )

    @app.get("/", response_class=HTMLResponse)
    def dashboard() -> str:
        return DASHBOARD

    def public_video_status() -> dict[str, object]:
        source = runtime.status()
        latest = source.get("latestFrame")
        video_ready = bool(source.get("videoReady"))
        robot_connected = bool(source.get("connected"))
        return {
            "robot_id": robot_id,
            "status": (
                "online"
                if video_ready
                else "degraded"
                if robot_connected
                else "offline"
            ),
            "robot_connected": robot_connected,
            "video_connected": robot_connected and source.get("videoState") != "disabled",
            "streaming": video_ready,
            "fps": 0.0 if latest is None else latest.get("fps", 0.0),
            "width": None if latest is None else latest.get("width"),
            "height": None if latest is None else latest.get("height"),
            "last_frame_age_ms": source.get("frameAgeMs"),
            "frame_count": source.get("frameCount", 0),
            "dropped_frame_count": source.get("droppedFrameCount", 0),
            "clients": source.get("videoClientCount", 0),
            "timestamp": _now_iso(),
        }

    @app.get("/healthz")
    def healthz() -> JSONResponse:
        # Process liveness is deliberately independent from video freshness.
        # Callers must use /api/v1/video/status to decide whether video is live.
        return JSONResponse(
            content={
                "status": "ok",
                "service": GATEWAY_ID,
                "version": SERVICE_VERSION,
                "timestamp": _now_iso(),
            },
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/api/v1/video/status")
    def video_status() -> JSONResponse:
        return JSONResponse(
            content=public_video_status(),
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/api/v1/robot/video")
    def video_discovery(request: Request) -> JSONResponse:
        state = public_video_status()
        return JSONResponse(
            content={
                "robot_id": robot_id,
                "status": state["status"],
                "video": {
                    "available": state["streaming"],
                    "protocol": "mjpeg",
                    "stream_url": str(request.url_for("stream")),
                    "width": state["width"],
                    "height": state["height"],
                    "fps": state["fps"],
                },
            },
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/status")
    def status() -> dict:
        source = runtime.status()
        latest = source["latestFrame"]
        return {
            "success": True,
            "ok": True,
            "apiVersion": API_VERSION,
            "serviceVersion": SERVICE_VERSION,
            "serviceId": SERVICE_ID,
            "runtimeId": RUNTIME_ID,
            "timestamp": _now_iso(),
            "data": {
                "serviceState": "running",
                "videoState": source["videoState"],
                "connectionMode": "Go2 STA / WebRTC (shared runtime)",
                "robotIp": source["robotIp"],
                "connected": source["connected"],
                "connectionState": source.get("connectionState"),
                "transportHealthState": source.get("transportHealthState"),
                "watchdogPolicy": source.get("watchdogPolicy"),
                "multiSignalStaleSeconds": source.get("multiSignalStaleSeconds"),
                "peerConnectionState": source.get("peerConnectionState"),
                "iceConnectionState": source.get("iceConnectionState"),
                "connectedSince": source.get("connectedSince"),
                "lastDisconnectAt": source.get("lastDisconnectAt"),
                "lastDisconnectReason": source.get("lastDisconnectReason"),
                "diagnosticReason": source.get("diagnosticReason"),
                "recentDisconnects": source.get("recentDisconnects", []),
                "lastReconnectAt": source.get("lastReconnectAt"),
                "staleTimeoutSeconds": source.get("staleTimeoutSeconds"),
                "hasFrame": source["videoReady"],
                "lastFrameAt": None if latest is None else latest["capturedAt"],
                "frameAgeMs": source["frameAgeMs"],
                "frameAgeSeconds": source.get("frameAgeSeconds"),
                "frameCount": source["frameCount"],
                "rawFrameCount": source.get("rawFrameCount", 0),
                "encodedFrameCount": source.get("encodedFrameCount", 0),
                "lastRawFrameAt": source.get("lastRawFrameAt"),
                "lastEncodedFrameAt": source.get("lastEncodedFrameAt"),
                "rawFrameAgeSeconds": source.get("rawFrameAgeSeconds"),
                "encodedFrameAgeSeconds": source.get("encodedFrameAgeSeconds"),
                "encodeQueueDepth": source.get("encodeQueueDepth", 0),
                "droppedFrameCount": source.get("droppedFrameCount", 0),
                "encodeDurationMsLast": source.get("encodeDurationMsLast"),
                "encodeDurationMsMax": source.get("encodeDurationMsMax"),
                "encodeDurationMsEwma": source.get("encodeDurationMsEwma"),
                "lastSportStateAt": source.get("lastSportStateAt"),
                "sportStateAgeSeconds": source.get("sportStateAgeSeconds"),
                "sportWatchdogArmed": source.get("sportWatchdogArmed", False),
                "captureFps": 0.0 if latest is None else latest["fps"],
                "fps": 0.0 if latest is None else latest["fps"],
                "resolution": (
                    None
                    if latest is None
                    else {"width": latest["width"], "height": latest["height"]}
                ),
                "source": {
                    "device": "Go2",
                    "networkMode": "STA",
                    "transport": "WebRTC",
                    "robotIp": source["robotIp"],
                    "connectionOwner": source["connectionOwner"],
                },
                "clientCount": source["videoClientCount"],
                "errorCount": source["videoErrorCount"],
                "reconnectCount": source.get("reconnectCount", 0),
                "successfulConnectionCount": source.get(
                    "successfulConnectionCount", 0
                ),
                "disconnectCount": source.get("disconnectCount", 0),
                "lastErrorCode": None if source["lastVideoError"] is None else "VIDEO_ERROR",
                "lastError": source["lastVideoError"],
                "error": (
                    None
                    if source["lastVideoError"] is None
                    else {"code": "VIDEO_ERROR", "message": source["lastVideoError"]}
                ),
                "latestFrame": latest,
                "dataChannelReady": source["dataChannelReady"],
                "sportStateReady": source["sportStateReady"],
                "dataHealthState": source.get("dataHealthState"),
                "dataDegradedReason": source.get("dataDegradedReason"),
                "videoHealthState": source.get("videoHealthState"),
                "videoDegradedReason": source.get("videoDegradedReason"),
                "firstRawFrameReceived": source.get("firstRawFrameReceived", False),
                "firstEncodedFrameProduced": source.get(
                    "firstEncodedFrameProduced", False
                ),
                "videoWatchdogArmed": source.get("videoWatchdogArmed", False),
                "videoWatchdogPolicy": source.get("videoWatchdogPolicy"),
                "videoWatchdog": source.get("videoWatchdog", {}),
                "motionReady": source.get("motionReady", False),
                "connectionCount": source["connectionCount"],
            },
        }

    @app.get("/snapshot")
    def snapshot() -> Response:
        source = runtime.status()
        latest = runtime.latest_frame()
        current_frame = getattr(runtime, "current_frame", None)
        frame = (
            current_frame()
            if callable(current_frame)
            else latest if source.get("videoReady") else None
        )
        if frame is None or not source.get("videoReady"):
            code = "video_unavailable" if latest is None else "video_frame_stale"
            return JSONResponse(
                status_code=503,
                content={
                    "success": False,
                    "code": code,
                    "message": "No current wireless camera frame is available.",
                    "lastFrameAt": (
                        None if latest is None else latest.captured_at
                    ),
                    "frameAgeSeconds": source.get("frameAgeSeconds"),
                },
            )
        return Response(
            frame.jpeg,
            media_type="image/jpeg",
            headers={"X-Frame-Seq": str(frame.sequence)},
        )

    @app.get("/debug/follow-target")
    def follow_target_debug() -> dict[str, object]:
        if follow_target_forwarder is None:
            return {
                "enabled": False,
                "running": False,
                "destination": None,
                "state_age_ms": None,
                "state": None,
            }
        return follow_target_forwarder.debug_status()

    @app.get("/api/robot/status")
    def robot_status() -> JSONResponse:
        if companion_control is None:
            return _control_unavailable()
        return JSONResponse(
            content=_ok("Robot status loaded.", companion_control.robot_status())
        )

    @app.get("/api/v1/robot/companion/status")
    def companion_status() -> JSONResponse:
        if companion_control is None:
            return _control_unavailable()
        return JSONResponse(
            content=_ok(
                "Companion status loaded.", companion_control.companion_status()
            )
        )

    @app.post("/api/v1/robot/companion/start")
    def companion_start() -> JSONResponse:
        if companion_control is None:
            return _control_unavailable()
        try:
            status = companion_control.start_companion()
        except WirelessCompanionControlError as exc:
            return JSONResponse(
                status_code=exc.status_code,
                content={
                    "success": False,
                    "code": exc.code,
                    "message": exc.message,
                },
            )
        return JSONResponse(content=_ok("Companion start accepted.", status))

    @app.post("/api/v1/robot/companion/stop")
    def companion_stop() -> JSONResponse:
        if companion_control is None:
            return _control_unavailable()
        try:
            status = companion_control.stop_companion()
        except WirelessCompanionControlError as exc:
            return JSONResponse(
                status_code=exc.status_code,
                content={
                    "success": False,
                    "code": exc.code,
                    "message": exc.message,
                },
            )
        return JSONResponse(content=_ok("Companion stopped.", status))

    @app.post("/api/v1/robot/companion/resume")
    def companion_resume() -> JSONResponse:
        if companion_control is None:
            return _control_unavailable()
        return _control_response(
            "Companion resume accepted.", companion_control.resume_companion
        )

    @app.post("/api/v1/robot/companion/intent")
    def companion_intent(payload: dict[str, object]) -> JSONResponse:
        if companion_control is None:
            return _control_unavailable()
        return _control_response(
            "Voice intent applied.",
            lambda: companion_control.apply_voice_intent(
                str(payload.get("intent") or "")
            ),
        )

    @app.post("/api/v1/robot/companion/risk-event")
    def companion_risk_event(payload: dict[str, object]) -> JSONResponse:
        if companion_control is None:
            return _control_unavailable()
        return _control_response(
            "Risk event applied.",
            lambda: companion_control.ingest_risk_event(payload),
        )

    @app.post("/api/v1/robot/companion/no-response")
    def companion_no_response() -> JSONResponse:
        if companion_control is None:
            return _control_unavailable()
        return _control_response(
            "No-response stage advanced.", companion_control.record_no_response
        )

    @app.post("/api/v1/robot/companion/manual")
    def companion_manual(payload: dict[str, object]) -> JSONResponse:
        if companion_control is None:
            return _control_unavailable()
        return _control_response(
            "Manual key applied.",
            lambda: companion_control.manual_key(str(payload.get("key") or "")),
        )

    @app.post("/api/v1/robot/companion/manual/release")
    def companion_manual_release() -> JSONResponse:
        if companion_control is None:
            return _control_unavailable()
        return _control_response(
            "Manual control released; explicit START or RESUME is required.",
            companion_control.release_manual,
        )

    @app.post("/api/v1/robot/companion/reset-demo")
    def companion_reset_demo() -> JSONResponse:
        if companion_control is None:
            return _control_unavailable()
        return _control_response(
            "Demo lifecycle reset without restarting transports.",
            companion_control.reset_demo,
        )

    @app.get("/api/v1/robot/video/stream", include_in_schema=False)
    @app.get("/stream.mjpg", name="stream")
    def stream(frames: Optional[int] = None) -> StreamingResponse:
        boundary = "go2frame"

        async def generate():
            sent = 0
            last_sequence = -1
            runtime.register_video_client()
            try:
                while True:
                    source = runtime.status()
                    if not source.get("videoReady"):
                        if not source.get("connected") or source.get(
                            "videoState"
                        ) in {"offline", "stalled"}:
                            return
                        await asyncio.sleep(0.02)
                        continue
                    current_frame = getattr(runtime, "current_frame", None)
                    frame = (
                        current_frame()
                        if callable(current_frame)
                        else runtime.latest_frame()
                    )
                    if frame is not None and frame.sequence != last_sequence:
                        last_sequence = frame.sequence
                        yield (
                            f"--{boundary}\r\nContent-Type: image/jpeg\r\n"
                            f"Content-Length: {len(frame.jpeg)}\r\n\r\n"
                        ).encode("ascii") + frame.jpeg + b"\r\n"
                        sent += 1
                        if frames is not None and sent >= frames:
                            return
                    await asyncio.sleep(0.02)
            finally:
                runtime.unregister_video_client()

        return StreamingResponse(
            generate(), media_type=f"multipart/x-mixed-replace; boundary={boundary}"
        )

    return app


def _ok(message: str, data: dict[str, object]) -> dict[str, object]:
    return {"success": True, "code": 0, "message": message, "data": data}


def _control_unavailable() -> JSONResponse:
    return JSONResponse(
        status_code=503,
        content={
            "success": False,
            "code": "COMPANION_CONTROL_UNAVAILABLE",
            "message": "Wireless companion control is not attached to this Runtime.",
        },
    )


def _control_response(message: str, operation) -> JSONResponse:
    try:
        status = operation()
    except WirelessCompanionControlError as exc:
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "success": False,
                "code": exc.code,
                "message": exc.message,
            },
        )
    except (RuntimeError, ValueError) as exc:
        return JSONResponse(
            status_code=409,
            content={
                "success": False,
                "code": "CONTROL_REJECTED",
                "message": str(exc),
            },
        )
    return JSONResponse(content=_ok(message, status))


DASHBOARD = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Go2 统一无线 Runtime</title><style>
body{margin:0;background:#111318;color:#eef2f7;font-family:Arial,"Microsoft YaHei",sans-serif}header{padding:16px 20px;background:#191d24;border-bottom:1px solid #333942;display:flex;justify-content:space-between}h1{font-size:20px;margin:0}main{padding:16px;display:grid;grid-template-columns:minmax(0,1fr) 320px;gap:16px}.video{background:#050608;border:1px solid #333942;border-radius:6px;overflow:hidden}.video img{display:block;width:100%;height:calc(100vh - 90px);object-fit:contain}.panel{background:#191d24;border:1px solid #333942;border-radius:6px;padding:14px}.row{display:flex;justify-content:space-between;padding:10px 0;border-bottom:1px solid #333942}.label{color:#aeb7c5}.ok{color:#61df8a}.bad{color:#ff7070}@media(max-width:850px){main{grid-template-columns:1fr}.video img{height:55vh}}
</style></head><body><header><h1>Go2 无线视频 + 运动</h1><span id="state" class="bad">正在连接</span></header><main><section class="video"><img src="/stream.mjpg" alt="Go2 第一视角"></section><aside class="panel"><div class="row"><span class="label">唯一连接</span><span id="connections">-</span></div><div class="row"><span class="label">DataChannel</span><span id="data">-</span></div><div class="row"><span class="label">SportState</span><span id="sport">-</span></div><div class="row"><span class="label">画面</span><span id="frame">-</span></div><div class="row"><span class="label">帧率</span><span id="fps">-</span></div><div class="row"><span class="label">错误</span><span id="error">-</span></div></aside></main><script>
async function refresh(){try{const j=await(await fetch('/status',{cache:'no-store'})).json();const d=j.data;const f=d.latestFrame;state.textContent=d.hasFrame?'无线视频在线':(d.connected?'等待画面':'正在连接');state.className=d.hasFrame?'ok':'bad';connections.textContent=`${d.connectionCount}（应为1）`;data.textContent=d.dataChannelReady?'READY':'NOT READY';sport.textContent=d.sportStateReady?'READY':'NOT READY';frame.textContent=f?`${f.width}x${f.height} #${f.sequence}`:'-';fps.textContent=Number(d.captureFps||0).toFixed(1);error.textContent=d.lastError||'无'}catch(e){state.textContent='服务离线';state.className='bad'}}refresh();setInterval(refresh,1000)
</script></body></html>"""
