"""Frame capture tool for diagnosing physical meters."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from aiohttp import ClientSession
from aiohttp.test_utils import TestServer

TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))
sys.path.insert(0, str(TOOLS / "dev"))

import capture_frames
from mock_device.meter import Meter
from mock_device.server import MeterServer


def _enable_sockets() -> None:
    """pytest-socket's marker is undone by the Home Assistant plugin hook."""
    try:
        from pytest_socket import enable_socket
    except ImportError:
        return
    enable_socket()


def reading(t: float, **data: object) -> dict[str, object]:
    return {"t": t, "type": "measurement", "data": data}


def test_summary_reports_cadence_shape_and_silence() -> None:
    frames = [
        {"t": 0.1, "type": "device", "data": {"product_type": "HWE-KWH3"}},
        reading(0.2, power_w=0, voltage_l1_v=230.0, energy_export_kwh=5),
        reading(1.2, power_w=0, voltage_l1_v=230.0, energy_export_kwh=5),
        reading(2.2, power_w=0, voltage_l1_v=230.1),
        reading(5.2, power_w=None, voltage_l1_v=230.1),
        {"t": 6.0, "type": "system", "data": {"wifi_rssi_db": -60}},
    ]
    summary = capture_frames.summarize(frames, 70.0)
    measurement = summary["measurement"]
    assert summary["frames"] == {"device": 1, "measurement": 4, "system": 1}
    assert measurement["count"] == 4
    assert measurement["interval_s"] == {"min": 1.0, "median": 1.0, "max": 3.0}
    assert measurement["longest_silence_s"] == 64.8
    assert measurement["stale_watchdog_would_trip"] is True
    assert measurement["distinct_field_sets"] == 2
    assert measurement["fields_always"] == ["power_w", "voltage_l1_v"]
    assert measurement["fields_sometimes"] == ["energy_export_kwh"]
    assert measurement["fields_null"] == ["power_w"]
    assert measurement["repeated_frames"] == 1


def test_summary_without_measurements() -> None:
    summary = capture_frames.summarize([], 30.0)
    assert summary["measurement"]["count"] == 0
    assert summary["measurement"]["interval_s"] is None
    assert summary["measurement"]["longest_silence_s"] == 30.0
    assert summary["measurement"]["stale_watchdog_would_trip"] is False


def test_redact_keeps_structure() -> None:
    data = {
        "serial": "5c2faf15ad1c",
        "wifi_ssid": "home",
        "power_w": 5,
        "unique_id": None,
        "external": [{"unique_id": "4730", "value": 1.5}],
    }
    assert capture_frames.redact(data) == {
        "serial": "**REDACTED**",
        "wifi_ssid": "**REDACTED**",
        "power_w": 5,
        "unique_id": None,
        "external": [{"unique_id": "**REDACTED**", "value": 1.5}],
    }


def test_summarize_an_earlier_capture(tmp_path: Path, capsys) -> None:
    path = tmp_path / "frames.jsonl"
    path.write_text(
        "\n".join(json.dumps(reading(t, power_w=1)) for t in (0.5, 1.5, 2.5)) + "\n",
        encoding="utf-8",
    )
    assert capture_frames.main(["--summarize", str(path)]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["duration_s"] == 2.5
    assert summary["measurement"]["repeated_frames"] == 2


def test_host_is_required_without_summarize() -> None:
    with pytest.raises(SystemExit):
        capture_frames.parse_args([])


@pytest.mark.enable_socket
async def test_pair_record_and_unpair_against_the_mock_meter(tmp_path: Path) -> None:
    _enable_sockets()
    meter = Meter("c0ffee000001", "3phase", push_interval=0.05)
    # Own directories: the mock persists tokens per serial in its work dir.
    server = MeterServer(meter, tmp_path, tmp_path)
    messages: list[str] = []
    async with TestServer(server.https_app()) as api, ClientSession() as session:
        base = api.make_url("/")
        with patch.object(meter, "button_open", side_effect=[False, True, True]):
            token = await capture_frames.pair(
                session, base, "capture-test", False, interval=0, say=messages.append
            )
        assert "Wi-Fi pair button" in messages[0]
        assert meter.authorized(f"Bearer {token}")

        socket = await capture_frames.open_stream(session, base, token, False)
        written: list[dict[str, object]] = []
        try:
            frames, duration = await capture_frames.record(socket, 0.4, written.append)
        finally:
            await socket.close()
        assert written == frames
        assert duration >= 0.4
        kinds = {frame["type"] for frame in frames}
        assert {"device", "measurement", "system"} <= kinds
        device = next(frame for frame in frames if frame["type"] == "device")
        assert device["data"]["serial"] == "**REDACTED**"
        summary = capture_frames.summarize(frames, duration)
        assert summary["measurement"]["count"] >= 2

        assert await capture_frames.unpair(session, base, token, False)
        assert not meter.authorized(f"Bearer {token}")


@pytest.mark.enable_socket
async def test_rejected_token_closes_the_socket(tmp_path: Path) -> None:
    _enable_sockets()
    meter = Meter("c0ffee000002", "3phase", push_interval=30)
    server = MeterServer(meter, tmp_path, tmp_path)
    async with TestServer(server.https_app()) as api, ClientSession() as session:
        with pytest.raises((PermissionError, ConnectionError)):
            await capture_frames.open_stream(session, api.make_url("/"), "bad", False)


def test_unreachable_meter_reports_one_line(capsys) -> None:
    async def refuse(_args: object) -> None:
        raise ConnectionRefusedError(111, "Connect call failed")

    with patch.object(capture_frames, "capture", refuse):
        assert capture_frames.main(["192.168.1.50:8443", "--token", "x"]) == 1
    assert "ConnectionRefusedError" in capsys.readouterr().err
