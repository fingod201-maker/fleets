"""rd-linux runtime smoke test under Xvfb (CI verification of the T1/T2 input path).

Starts Xvfb + a minimal WebSocket rd broker, launches rd-linux against it, sends
move/click/type input messages, and asserts the helper stays alive and processes
them. This validates the compiled X11/XTEST input path runs under a real display
without crashing. The <=150 ms end-to-end claim still requires live hardware.

usage: python tests/test_rd_linux_runtime.py [rd-linux-binary]
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
from contextlib import suppress
from pathlib import Path

BROKER_PORT = 8765
SESSION = "ci-session"
TOKEN = "ci-token"


async def mock_broker(stop_event: asyncio.Event, received: list[str]) -> None:
    """Minimal rd broker: accepts the helper WS, relays driver input to it.

    Connection order: rd-linux (helper) connects first, then the test driver.
    Messages from the driver are forwarded to the helper so the XTEST input
    path actually executes.
    """

    import websockets

    helper_ws: list[object] = []  # single-element holder, mutated under lock
    lock = asyncio.Lock()

    async def handler(websocket):
        async with lock:
            if not helper_ws:
                helper_ws.append(websocket)
                received.append("connected")
            else:
                received.append("connected driver")

        try:
            async for message in websocket:
                received.append(message if isinstance(message, str) else f"<binary {len(message)}>")
                # forward driver input -> helper
                async with lock:
                    target = helper_ws[0] if helper_ws else None
                if target is not None and websocket is not target:
                    with suppress(websockets.exceptions.ConnectionClosed):
                        await target.send(message)
        except websockets.exceptions.ConnectionClosed:
            pass
        finally:
            async with lock:
                if websocket in helper_ws:
                    helper_ws.remove(websocket)

    async with websockets.serve(handler, "127.0.0.1", BROKER_PORT):
        stop_event.set()
        await stop_event.wait()


async def run_helper(binary: Path, received: list[str]) -> int:
    env = os.environ.copy()
    env["DISPLAY"] = ":99"
    proc = await asyncio.create_subprocess_exec(
        str(binary),
        "--host", "127.0.0.1",
        "--port", str(BROKER_PORT),
        "--session", SESSION,
        "--token", TOKEN,
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    # wait until the helper has connected (broker saw the connection)
    deadline = time.time() + 15
    while "connected" not in received and time.time() < deadline:
        await asyncio.sleep(0.05)
    if "connected" not in received:
        proc.kill()
        await proc.wait()
        raise RuntimeError("rd-linux never connected to the mock broker")

    # Send a battery of input messages through the broker -> helper.
    # We open a second WS connection to the broker to drive input.
    import websockets

    uri = f"ws://127.0.0.1:{BROKER_PORT}/rd/{SESSION}?token={TOKEN}"
    async with websockets.connect(uri) as driver:
        msgs = [
            {"t": "m", "x": 0.25, "y": 0.33},
            {"t": "b", "btn": 0, "down": 1},
            {"t": "b", "btn": 0, "down": 0},
            {"t": "b", "btn": 4, "down": 1},   # wheel up
            {"t": "b", "btn": 5, "down": 1},   # wheel down
            {"t": "k", "code": 65, "down": 1},  # 'A'
            {"t": "k", "code": 65, "down": 0},
            {"t": "txt", "text": "CI input test\n"},
        ]
        for m in msgs:
            await driver.send(json.dumps(m))
            await asyncio.sleep(0.05)

    # Give the helper a moment to process, then check it is still alive.
    await asyncio.sleep(0.5)
    if proc.returncode is not None:
        out, err = await proc.communicate()
        raise RuntimeError(
            "rd-linux exited early (rc="
            f"{proc.returncode})\nstdout={out.decode(errors='replace')}"
            f"\nstderr={err.decode(errors='replace')}"
        )

    proc.terminate()
    try:
        await asyncio.wait_for(proc.wait(), timeout=5)
    except TimeoutError:
        proc.kill()
        await proc.wait()
    return proc.returncode or 0


async def main() -> int:
    binary = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("/tmp/rd-out/rd-linux")
    if not binary.exists():
        print(f"rd-linux binary not found: {binary}", file=sys.stderr)
        return 2

    # Start Xvfb.
    xvfb = subprocess.Popen(
        ["Xvfb", ":99", "-screen", "0", "1280x720x24", "-nolisten", "tcp"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        # Wait for Xvfb to be ready.
        for _ in range(50):
            if Path("/tmp/.X11-unix/X99").exists():
                break
            await asyncio.sleep(0.1)
        else:
            print("Xvfb did not start", file=sys.stderr)
            return 2

        received: list[str] = []
        stop_event = asyncio.Event()
        broker_task = asyncio.create_task(mock_broker(stop_event, received))
        await stop_event.wait()

        try:
            rc = await run_helper(binary, received)
        finally:
            stop_event.set()
            broker_task.cancel()
            with suppress(asyncio.CancelledError):
                await broker_task

        print(f"received {len(received)} broker events; rd-linux rc={rc}")
        if rc != 0:
            return 1
        print("OK: rd-linux started under Xvfb, accepted input, stayed alive")
        return 0
    finally:
        xvfb.terminate()
        try:
            xvfb.wait(timeout=5)
        except subprocess.TimeoutExpired:
            xvfb.kill()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
