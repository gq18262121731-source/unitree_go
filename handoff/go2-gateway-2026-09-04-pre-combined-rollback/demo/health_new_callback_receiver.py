from __future__ import annotations

import argparse
import json
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class CallbackHandler(BaseHTTPRequestHandler):
    dump_json = False

    def do_GET(self) -> None:
        if self.path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"ok":true}')
            return
        self.send_error(404)

    def do_POST(self) -> None:
        if self.path != "/api/robot/callback":
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        try:
            payload = json.loads(body.decode("utf-8"))
        except json.JSONDecodeError:
            self.send_error(400, "invalid json")
            return

        stamp = datetime.now().strftime("%H:%M:%S")
        result = payload.get("result") or {}
        progress = payload.get("progress") or {}
        print(
            f"[{stamp}] task={payload.get('task_id')} "
            f"rev={payload.get('revision')} status={payload.get('status')} step={payload.get('step')} "
            f"external_task_id={payload.get('external_task_id')} "
            f"progress={progress.get('percent')} "
            f"camera={payload.get('camera')} voice={payload.get('voice')} "
            f"confirm={result.get('confirm')} error_code={result.get('errorCode')} "
            f"failure_step={result.get('failureStep')} finished={payload.get('finished')}",
            flush=True,
        )
        if self.dump_json:
            print(json.dumps(payload, ensure_ascii=False, indent=2), flush=True)

        self.send_response(204)
        self.end_headers()

    def log_message(self, format: str, *args) -> None:
        return


def main() -> None:
    parser = argparse.ArgumentParser(description="Receive and print Go2 task callbacks like health_new.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8088)
    parser.add_argument("--dump-json", action="store_true", help="Print the full callback payload after each summary line.")
    args = parser.parse_args()

    CallbackHandler.dump_json = args.dump_json
    server = ThreadingHTTPServer((args.host, args.port), CallbackHandler)
    print(f"health_new callback receiver listening on http://{args.host}:{args.port}/api/robot/callback")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopping callback receiver")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
