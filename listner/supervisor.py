from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

COMMANDS = [
    [sys.executable, "-m", "listner.worker"],
    [sys.executable, "-m", "listner.bot"],
]


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path not in ("/", "/health"):
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"Listner OK\\n")

    def log_message(self, _format: str, *_args: object) -> None:
        return


def start_health_server() -> ThreadingHTTPServer:
    port = int(os.getenv("PORT", "10000"))
    server = ThreadingHTTPServer(("0.0.0.0", port), HealthHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    print(f"supervisor: health server listening on {port}", flush=True)
    return server


def main() -> None:
    processes: list[subprocess.Popen[object]] = []
    stopping = False
    health = start_health_server()

    def stop(*_args: object) -> None:
        nonlocal stopping
        stopping = True
        health.shutdown()
        for process in processes:
            if process.poll() is None:
                process.terminate()
        deadline = time.monotonic() + 10
        for process in processes:
            remaining = max(0.0, deadline - time.monotonic())
            try:
                process.wait(timeout=remaining)
            except subprocess.TimeoutExpired:
                process.kill()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    try:
        processes = [subprocess.Popen(command) for command in COMMANDS]
        while not stopping:
            for index, process in enumerate(processes):
                return_code = process.poll()
                if return_code is not None:
                    print(
                        f"supervisor: process {index} exited with code {return_code}; stopping container",
                        flush=True,
                    )
                    stop()
                    raise SystemExit(return_code or 1)
            time.sleep(2)
    finally:
        stop()


if __name__ == "__main__":
    main()
