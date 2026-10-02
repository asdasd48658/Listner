from __future__ import annotations

import signal
import subprocess
import sys
import time


COMMANDS = [
    [sys.executable, "-m", "listner.worker"],
    [sys.executable, "-m", "listner.bot"],
]


def main() -> None:
    processes: list[subprocess.Popen[object]] = []
    stopping = False

    def stop(*_args: object) -> None:
        nonlocal stopping
        stopping = True
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
