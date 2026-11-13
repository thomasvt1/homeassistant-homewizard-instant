"""Record what a physical HomeWizard meter sends over its API v2 WebSocket.

Home Assistant is not involved. The script pairs a temporary local user with a
button press, or reuses a token, subscribes to the same topics as the
integration and writes every frame to a JSON Lines file. At the end it prints
how often measurements arrive, whether frames carry every field, and whether
the integration's 60 s stale watchdog would have marked the meter unavailable.

    python3 tools/dev/capture_frames.py 192.168.1.50 --minutes 10
    python3 tools/dev/capture_frames.py --summarize homewizard-frames.jsonl

Serials, meter identifiers and the Wi-Fi SSID are redacted in the file and
the output. A user paired by the script is deleted again unless --keep-user
is given.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import secrets
import ssl
import statistics
import sys
import time
from collections import Counter
from collections.abc import Callable, Iterable
from itertools import pairwise
from pathlib import Path
from typing import Any

import aiohttp
from homewizard_energy.v2.cacert import CACERT
from yarl import URL

TOPICS = ("device", "measurement", "system")
# Written last, with the elapsed time at which the recording stopped.
END = "<end>"
REDACTED = "**REDACTED**"
REDACT_KEYS = frozenset({"id", "serial", "token", "unique_id", "wifi_ssid"})
# Matches STALE_AFTER in custom_components/homewizard_instant/const.py.
STALE_AFTER_S = 60.0
PAIR_TIMEOUT_S = 60.0
_HEADERS = {"X-Api-Version": "2"}
_STOP = {
    aiohttp.WSMsgType.CLOSE,
    aiohttp.WSMsgType.CLOSING,
    aiohttp.WSMsgType.CLOSED,
    aiohttp.WSMsgType.ERROR,
}

type Frame = dict[str, Any]


def redact(value: Any) -> Any:
    """Replace identifying values, keeping the structure and every key."""
    if isinstance(value, dict):
        return {
            key: REDACTED if key in REDACT_KEYS and item is not None else redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value


def ssl_context(ca_file: str | None = None) -> ssl.SSLContext:
    """Trust the HomeWizard CA, like the integration, without a hostname check."""
    cadata = CACERT
    if ca_file:
        cadata = f"{cadata.rstrip()}\n{Path(ca_file).read_text(encoding='utf-8')}"
    context = ssl.create_default_context(cadata=cadata)
    context.verify_flags = ssl.VERIFY_X509_PARTIAL_CHAIN
    context.check_hostname = False
    context.verify_mode = ssl.CERT_REQUIRED
    return context


async def pair(
    session: aiohttp.ClientSession,
    base: URL,
    name: str,
    tls: ssl.SSLContext | bool,
    *,
    timeout: float = PAIR_TIMEOUT_S,
    interval: float = 2.0,
    say: Callable[[str], None] = print,
) -> str:
    """Request a token until the meter's pairing window opens."""
    say(
        "Press the meter's button now. On a kWh meter, hold the Wi-Fi pair "
        "button for 1 to 3 seconds."
    )
    deadline = time.monotonic() + timeout
    while True:
        async with session.post(
            base.with_path("/api/user"),
            json={"name": f"local/{name}"},
            headers=_HEADERS,
            ssl=tls,
        ) as response:
            body = await response.json(content_type=None)
            if response.status == 200:
                token = body["token"]
                assert isinstance(token, str)
                say(f"Paired as local/{name}.")
                return token
            if response.status != 403:
                raise RuntimeError(f"Pairing failed with {response.status}: {body}")
        if time.monotonic() >= deadline:
            raise TimeoutError(f"No button press within {timeout:.0f} s")
        await asyncio.sleep(interval)


async def unpair(
    session: aiohttp.ClientSession, base: URL, token: str, tls: ssl.SSLContext | bool
) -> bool:
    """Delete the user that owns this token."""
    async with session.delete(
        base.with_path("/api/user"),
        headers={**_HEADERS, "Authorization": f"Bearer {token}"},
        ssl=tls,
    ) as response:
        return response.status == 204


async def _next_json(socket: aiohttp.ClientWebSocketResponse) -> Frame:
    message = await socket.receive()
    if message.type != aiohttp.WSMsgType.TEXT:
        raise ConnectionError(
            f"WebSocket ended during the handshake: {message.type.name}"
        )
    payload = json.loads(message.data)
    if not isinstance(payload, dict):
        raise ConnectionError(f"Unexpected handshake frame: {payload!r}")
    return payload


async def open_stream(
    session: aiohttp.ClientSession,
    base: URL,
    token: str,
    tls: ssl.SSLContext | bool,
    *,
    timeout: float = 30.0,
) -> aiohttp.ClientWebSocketResponse:
    """Authenticate and subscribe to the integration's topics."""
    scheme = "wss" if base.scheme == "https" else "ws"
    socket = await session.ws_connect(
        base.with_scheme(scheme).with_path("/api/ws"),
        ssl=tls,
        heartbeat=15,
        headers=_HEADERS,
    )
    try:
        async with asyncio.timeout(timeout):
            message = await _next_json(socket)
            if message.get("type") != "authorization_requested":
                raise ConnectionError(f"Expected authorization_requested: {message!r}")
            await socket.send_json({"type": "authorization", "data": token})
            message = await _next_json(socket)
            if message.get("type") != "authorized":
                raise PermissionError(f"Authorization rejected: {redact(message)!r}")
            for topic in TOPICS:
                await socket.send_json({"type": "subscribe", "data": topic})
    except BaseException:
        await socket.close()
        raise
    return socket


async def record(
    socket: aiohttp.ClientWebSocketResponse,
    seconds: float,
    sink: Callable[[Frame], None],
    clock: Callable[[], float] = time.monotonic,
) -> tuple[list[Frame], float]:
    """Collect redacted frames until the time is up or the meter closes.

    The last frame is always an END marker holding the elapsed time, also when
    the capture is cancelled, so a summary of the file still counts the
    silence after the final measurement.
    """
    start = clock()
    frames: list[Frame] = []
    try:
        while (remaining := start + seconds - clock()) > 0:
            try:
                message = await asyncio.wait_for(socket.receive(), remaining)
            except TimeoutError:
                break
            elapsed = round(clock() - start, 3)
            if message.type == aiohttp.WSMsgType.TEXT:
                try:
                    payload: Any = json.loads(message.data)
                except ValueError:
                    payload = {"type": "<invalid json>", "data": message.data[:200]}
                if not isinstance(payload, dict):
                    payload = {"type": "<not an object>", "data": payload}
                frame = {
                    "t": elapsed,
                    "type": str(payload.get("type")),
                    "data": redact(payload.get("data")),
                }
            else:
                frame = {"t": elapsed, "type": f"<{message.type.name}>", "data": None}
            frames.append(frame)
            sink(frame)
            if message.type in _STOP:
                break
    finally:
        duration = round(clock() - start, 3)
        end: Frame = {"t": duration, "type": END, "data": None}
        frames.append(end)
        sink(end)
    return frames, duration


def _round(value: float) -> float:
    return round(value, 2)


def summarize(frames: Iterable[Frame], duration: float) -> dict[str, Any]:
    """Describe the measurement cadence and frame shape."""
    frames = list(frames)
    readings = [
        frame
        for frame in frames
        if frame["type"] == "measurement" and isinstance(frame.get("data"), dict)
    ]
    times = [frame["t"] for frame in readings]
    gaps = [later - earlier for earlier, later in pairwise(times)]
    keys = [set(frame["data"]) for frame in readings]
    seen = sorted(set().union(*keys)) if keys else []
    always = [key for key in seen if all(key in frame for frame in keys)]
    silences = [times[0], *gaps, duration - times[-1]] if times else [duration]
    longest = max(silences)
    return {
        "duration_s": _round(duration),
        "frames": dict(
            Counter(frame["type"] for frame in frames if frame["type"] != END)
        ),
        "measurement": {
            "count": len(readings),
            "interval_s": (
                {
                    "min": _round(min(gaps)),
                    "median": _round(statistics.median(gaps)),
                    "max": _round(max(gaps)),
                }
                if gaps
                else None
            ),
            "longest_silence_s": _round(longest),
            "stale_watchdog_would_trip": longest >= STALE_AFTER_S,
            "distinct_field_sets": len({tuple(sorted(item)) for item in keys}),
            "fields_always": always,
            "fields_sometimes": [key for key in seen if key not in always],
            "fields_null": sorted(
                {
                    key
                    for frame in readings
                    for key, value in frame["data"].items()
                    if value is None
                }
            ),
            "repeated_frames": sum(
                1
                for earlier, later in pairwise(readings)
                if earlier["data"] == later["data"]
            ),
        },
    }


def load(path: Path) -> tuple[list[Frame], float]:
    """Read frames written by an earlier capture.

    The END marker gives the real stop time. A file without one (the process
    was killed) only lasts until its last frame, so trailing silence is lost.
    """
    frames = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    ends = [frame["t"] for frame in frames if frame["type"] == END]
    if ends:
        return frames, ends[-1]
    return frames, max((frame["t"] for frame in frames), default=0.0)


def _progress(frame: Frame) -> None:
    data = frame["data"]
    detail = f"{len(data)} fields" if isinstance(data, dict) else ""
    print(f"{frame['t']:9.3f}s  {frame['type']:<12} {detail}", flush=True)


async def capture(args: argparse.Namespace) -> dict[str, Any]:
    tls = await asyncio.to_thread(ssl_context, args.ca_file)
    base = URL(f"https://{args.host}")
    path = Path(args.out or f"homewizard-frames-{time.strftime('%Y%m%d-%H%M%S')}.jsonl")
    token = args.token or os.environ.get("HOMEWIZARD_TOKEN")
    async with aiohttp.ClientSession() as session:
        paired = token is None
        if token is None:
            token = await pair(
                session,
                base,
                f"capture-{secrets.token_hex(3)}",
                tls,
                timeout=args.pair_timeout,
            )
        try:
            socket = await open_stream(session, base, token, tls)
            print(f"Recording for {args.minutes:g} min to {path}", flush=True)
            # Each frame is one short line, written as it arrives so an
            # interrupted capture still leaves a usable file.
            handle = await asyncio.to_thread(path.open, "w", encoding="utf-8")

            def sink(frame: Frame) -> None:
                handle.write(json.dumps(frame) + "\n")
                handle.flush()
                _progress(frame)

            try:
                frames, duration = await record(socket, args.minutes * 60, sink)
            finally:
                await socket.close()
                await asyncio.to_thread(handle.close)
        finally:
            if paired and not args.keep_user:
                try:
                    removed = await unpair(session, base, token, tls)
                except (aiohttp.ClientError, OSError):
                    removed = False
                print(
                    "Removed the temporary user."
                    if removed
                    else "Could not remove the temporary user; delete it in the HomeWizard Energy app."
                )
    return summarize(frames, duration)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "host", nargs="?", help="meter IP address or host name, optionally with :port"
    )
    parser.add_argument("--minutes", type=float, default=10, help="recording length")
    parser.add_argument("--out", help="JSON Lines file (default: timestamped name)")
    parser.add_argument(
        "--token", help="existing API v2 token (or set HOMEWIZARD_TOKEN); skips pairing"
    )
    parser.add_argument(
        "--keep-user", action="store_true", help="keep the user the script paired"
    )
    parser.add_argument(
        "--pair-timeout",
        type=float,
        default=PAIR_TIMEOUT_S,
        help="seconds to wait for the button press (default: %(default)g)",
    )
    parser.add_argument(
        "--ca-file", help="extra CA to trust, for the lab's emulated meters"
    )
    parser.add_argument(
        "--summarize", metavar="FILE", type=Path, help="summarize an earlier capture"
    )
    args = parser.parse_args(argv)
    if args.summarize is None and args.host is None:
        parser.error("a host is required unless --summarize is given")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.summarize is not None:
        summary = summarize(*load(args.summarize))
    else:
        try:
            summary = asyncio.run(capture(args))
        except (aiohttp.ClientError, OSError, TimeoutError, RuntimeError) as err:
            # ConnectionError and PermissionError from the handshake are OSErrors.
            print(f"error: {type(err).__name__}: {err}", file=sys.stderr)
            return 1
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
