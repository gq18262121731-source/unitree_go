from __future__ import annotations

import os
import time
from typing import Optional

import cv2
import numpy as np
from fastapi import FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import Response, StreamingResponse

from .frame_store import ReceivedFrame, ReceiverStore, latency_ms, now_iso
from .schemas import Heartbeat


MAX_FRAME_BYTES = int(os.getenv("MOCK_RECEIVER_MAX_FRAME_BYTES", "1048576"))
UPLOAD_TOKEN = os.getenv("GO2_UPLOAD_TOKEN", "local-test-token")


def create_app(store: ReceiverStore | None = None) -> FastAPI:
    store = store or ReceiverStore()
    app = FastAPI(title="Go2 Mock Video Receiver", version="0.1.0")
    app.state.store = store

    @app.post("/api/video/frame")
    async def receive_frame(
        robot_id: str = Form(...),
        camera_id: str = Form(...),
        frame_seq: int = Form(...),
        captured_at: str = Form(...),
        width: int = Form(...),
        height: int = Form(...),
        capture_fps: float = Form(...),
        file: UploadFile = File(...),
        x_upload_token: str = Header(...),
    ) -> dict:
        if x_upload_token != UPLOAD_TOKEN:
            store.reject()
            raise HTTPException(status_code=401, detail="invalid upload token")
        jpeg = await file.read()
        if not robot_id or not camera_id or frame_seq < 1:
            store.reject()
            raise HTTPException(status_code=422, detail="invalid metadata")
        if len(jpeg) > MAX_FRAME_BYTES:
            store.reject()
            raise HTTPException(status_code=413, detail="frame too large")
        if not is_jpeg(jpeg):
            store.reject()
            raise HTTPException(status_code=415, detail="file is not a JPEG")
        received_at = now_iso()
        frame = ReceivedFrame(
            jpeg=jpeg,
            robot_id=robot_id,
            camera_id=camera_id,
            frame_seq=frame_seq,
            captured_at=captured_at,
            received_at=received_at,
            width=width,
            height=height,
            latency_ms=latency_ms(captured_at),
        )
        store.accept(frame)
        return {"accepted": True, "frameSeq": frame_seq, "receivedAt": received_at, "latencyMs": frame.latency_ms}

    @app.post("/api/video/heartbeat")
    def heartbeat(payload: Heartbeat, x_upload_token: Optional[str] = Header(default=None)) -> dict:
        if x_upload_token not in (None, UPLOAD_TOKEN):
            raise HTTPException(status_code=401, detail="invalid upload token")
        store.heartbeat(payload.model_dump())
        return {"accepted": True, "receivedAt": now_iso()}

    @app.get("/latest.jpg")
    def latest_jpg() -> Response:
        frame = store.latest()
        if frame is None:
            raise HTTPException(status_code=404, detail="no frame")
        return Response(content=frame.jpeg, media_type="image/jpeg")

    @app.get("/stream.mjpg")
    def stream_mjpg(frames: Optional[int] = None) -> StreamingResponse:
        boundary = "receiverframe"

        def generate():
            last_seq = None
            sent = 0
            while True:
                frame = store.latest()
                if frame is not None and frame.frame_seq != last_seq:
                    last_seq = frame.frame_seq
                    yield (
                        f"--{boundary}\r\n"
                        "Content-Type: image/jpeg\r\n"
                        f"X-Frame-Seq: {frame.frame_seq}\r\n"
                        f"Content-Length: {len(frame.jpeg)}\r\n\r\n"
                    ).encode("ascii") + frame.jpeg + b"\r\n"
                    sent += 1
                    if frames is not None and sent >= frames:
                        break
                time.sleep(0.2)

        return StreamingResponse(generate(), media_type=f"multipart/x-mixed-replace; boundary={boundary}")

    @app.get("/status")
    def status() -> dict:
        return {"success": True, "data": store.status()}

    return app


def is_jpeg(jpeg: bytes) -> bool:
    if len(jpeg) < 4 or not jpeg.startswith(b"\xff\xd8") or not jpeg.endswith(b"\xff\xd9"):
        return False
    image = cv2.imdecode(np.frombuffer(jpeg, dtype=np.uint8), cv2.IMREAD_COLOR)
    return image is not None


app = create_app()
