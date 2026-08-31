from __future__ import annotations

import asyncio
from io import StringIO
from typing import Any

from rich.console import Console

from sonygeotag import monitor_tui
from sonygeotag.ble_probe import ObservedDevice
from sonygeotag.camera_monitor import MonitorPhase
from sonygeotag.camera_monitor import MonitorUpdate
from sonygeotag.sony_info import decode_characteristic

CAMERA_CONTROL_SERVICE = "8000cc00-cc00-ffff-ffff-ffffffffffff"
MODEL_UUID = "0000cc0b-0000-1000-8000-00805f9b34fb"
BATTERY_UUID = "0000cc10-0000-1000-8000-00805f9b34fb"


def test_textual_dashboard_renders_connected_camera_status(monkeypatch) -> None:
    readings = (
        decode_characteristic(
            service_uuid=CAMERA_CONTROL_SERVICE,
            uuid=MODEL_UUID,
            handle=1,
            value=b"ILCE-7CM2",
        ),
        decode_characteristic(
            service_uuid=CAMERA_CONTROL_SERVICE,
            uuid=BATTERY_UUID,
            handle=2,
            value=bytes.fromhex("12 00 00 02 03 00 01 00 05 00 00 00 00 56 00 00 00 00 01"),
        ),
    )
    update = MonitorUpdate(
        phase=MonitorPhase.CONNECTED,
        message="Read-only BLE session active",
        captured_at="2026-08-07T10:00:00.000+00:00",
        device=ObservedDevice(
            address="redacted-in-ui",
            name="ILCE-7CM2",
            local_name="ILCE-7CM2",
            rssi=-42,
            service_uuids=(),
            manufacturer_data={},
        ),
        readings=readings,
        poll_count=1,
    )

    async def fake_stream_camera_status(**kwargs: Any) -> None:
        on_update = kwargs["on_update"]
        assert callable(on_update)
        on_update(update)

    monkeypatch.setattr(monitor_tui, "stream_camera_status", fake_stream_camera_status)
    app = monitor_tui.CameraMonitorApp(
        targets=("ILCE-7CM2",),
        scan_timeout=1,
        connect_timeout=1,
        poll_interval=1,
        pair=True,
        duration=None,
    )

    async def run_test() -> None:
        async with app.run_test(size=(100, 40)) as pilot:
            await pilot.pause()
            connection = app.query_one("#connection")
            assert connection.has_class("phase-connected")
            console = Console(record=True, width=80, file=StringIO())
            console.print(monitor_tui._overview_table(update, update.summary))
            rendered = console.export_text()
            assert "ILCE-7CM2" in rendered
            assert "86%" in rendered
            assert "-42 dBm" in rendered

    asyncio.run(run_test())
