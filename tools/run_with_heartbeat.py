"""Run a command without an artificial deadline while exposing liveness.

The child output is streamed immediately.  Quiet periods produce heartbeat
messages and, after a configurable interval, a warning annotation.  A warning
never terminates the child: request-level timeouts remain the component's job.
"""

from __future__ import annotations

import argparse
import queue
import subprocess
import sys
import threading
import time


_EOF = object()

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
except (AttributeError, ValueError):
    pass


def _duration(seconds: float) -> str:
    total = max(0, int(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}h{minutes:02d}m{secs:02d}s"
    return f"{minutes}m{secs:02d}s"


def run(command: list[str], *, label: str, heartbeat_seconds: float,
        stall_warning_seconds: float) -> int:
    started = last_output = time.monotonic()
    lines: queue.Queue[object] = queue.Queue()
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )

    def read_output() -> None:
        assert process.stdout is not None
        for line in process.stdout:
            lines.put(line)
        lines.put(_EOF)

    threading.Thread(target=read_output, daemon=True).start()
    warned_for_silence = False
    try:
        while True:
            try:
                item = lines.get(timeout=heartbeat_seconds)
            except queue.Empty:
                now = time.monotonic()
                silent = now - last_output
                state = "quiet" if silent >= stall_warning_seconds else "running"
                print(
                    f"[heartbeat] {label} {state} — elapsed={_duration(now - started)} "
                    f"last_output={_duration(silent)} ago pid={process.pid}",
                    flush=True,
                )
                if state == "quiet" and not warned_for_silence:
                    print(
                        f"::warning::{label} 프로세스는 실행 중이지만 "
                        f"{_duration(silent)} 동안 진행 로그가 없습니다. "
                        "강제 종료하지 않고 내부 요청 timeout과 다음 진행 신호를 기다립니다.",
                        flush=True,
                    )
                    warned_for_silence = True
                continue

            if item is _EOF:
                break
            print(str(item), end="", flush=True)
            last_output = time.monotonic()
            warned_for_silence = False
    except KeyboardInterrupt:
        process.terminate()
        raise

    return_code = process.wait()
    elapsed = _duration(time.monotonic() - started)
    outcome = "completed" if return_code == 0 else "failed"
    print(f"[heartbeat] {label} {outcome} — elapsed={elapsed} exit={return_code}", flush=True)
    return return_code


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", default="command")
    parser.add_argument("--heartbeat-seconds", type=float, default=60.0)
    parser.add_argument("--stall-warning-seconds", type=float, default=300.0)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("a command is required after --")
    if args.heartbeat_seconds <= 0 or args.stall_warning_seconds <= 0:
        parser.error("heartbeat and stall-warning intervals must be positive")
    return run(
        command,
        label=args.label,
        heartbeat_seconds=args.heartbeat_seconds,
        stall_warning_seconds=args.stall_warning_seconds,
    )


if __name__ == "__main__":
    raise SystemExit(main())
