# Go2 Mock Video Receiver

Local receiver for simulated Go2 wireless camera uploads.

Run:

```bash
source ~/.venvs/go2-gateway/bin/activate
cd "/mnt/e/笨笨狗/go2_dev/go2-wireless-camera/mock_receiver"
python -m pip install -r requirements.txt
uvicorn app.main:app --host 127.0.0.1 --port 8092 --workers 1
```

Endpoints:

```text
POST /api/video/frame
POST /api/video/heartbeat
GET  /latest.jpg
GET  /stream.mjpg
GET  /status
```

Only the latest frame and the latest 100 metadata entries are retained.
