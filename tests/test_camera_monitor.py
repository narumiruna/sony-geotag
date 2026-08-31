from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import cast

from bleak.backends.device import BLEDevice

from sonygeotag.ble_probe import ObservedDevice
from sonygeotag.ble_probe import ScannedDevice
from sonygeotag.camera_monitor import MonitorPhase
from sonygeotag.camera_monitor import MonitorUpdate
from sonygeotag.camera_monitor import stream_camera_status

CAMERA_CONTROL_SERVICE = "8000cc00-cc00-ffff-ffff-ffffffffffff"
LOCATION_SERVICE = "8000dd00-dd00-ffff-ffff-ffffffffffff"
FIRMWARE_UUID = "0000cc0a-0000-1000-8000-00805f9b34fb"
MODEL_UUID = "0000cc0b-0000-1000-8000-00805f9b34fb"
BATTERY_UUID = "0000cc10-0000-1000-8000-00805f9b34fb"
LOCATION_CAPABILITIES_UUID = "0000dd21-0000-1000-8000-00805f9b34fb"
LOCATION_LOCK_UUID = "0000dd30-0000-1000-8000-00805f9b34fb"


@dataclass
class FakeCharacteristic:
    uuid: str
    handle: int
    properties: tuple[str, ...] = ("read",)


@dataclass
class FakeService:
    uuid: str
    characteristics: tuple[FakeCharacteristic, ...]


class StrictMonitorClient:
    def __init__(self, services: tuple[FakeService, ...], values: dict[str, bytes], operations: list[str]) -> None:
        self.services = services
        self.values = values
        self.operations = operations
        self.is_connected = True

    async def __aenter__(self) -> StrictMonitorClient:
        self.operations.append("connect")
        return self

    async def __aexit__(self, *_args: object) -> None:
        self.operations.append("disconnect")

    async def read_gatt_char(self, characteristic: FakeCharacteristic) -> bytes:
        self.operations.append(f"read:{characteristic.uuid}")
        return self.values[characteristic.uuid]

    async def write_gatt_char(self, *_args: object, **_kwargs: object) -> None:
        raise AssertionError("read-only monitor attempted write_gatt_char")

    async def start_notify(self, *_args: object, **_kwargs: object) -> None:
        raise AssertionError("read-only monitor attempted start_notify")


def scanned_camera() -> ScannedDevice:
    return ScannedDevice(
        device=cast("BLEDevice", object()),
        observation=ObservedDevice(
            address="00000000-1111-2222-3333-444444444444",
            name="ILCE-7CM2",
            local_name="ILCE-7CM2",
            rssi=-42,
            service_uuids=("00001800-0000-1000-8000-00805f9b34fb",),
            manufacturer_data={0x012D: bytes.fromhex("03 00 65 00")},
        ),
    )


def test_monitor_keeps_one_connection_and_polls_dynamic_values_without_writes() -> None:
    services = (
        FakeService(
            CAMERA_CONTROL_SERVICE,
            (
                FakeCharacteristic(FIRMWARE_UUID, 1),
                FakeCharacteristic(MODEL_UUID, 2),
                FakeCharacteristic(BATTERY_UUID, 3),
            ),
        ),
        FakeService(
            LOCATION_SERVICE,
            (
                FakeCharacteristic(LOCATION_CAPABILITIES_UUID, 4),
                FakeCharacteristic(LOCATION_LOCK_UUID, 5),
            ),
        ),
    )
    values = {
        FIRMWARE_UUID: b"2.01",
        MODEL_UUID: b"ILCE-7CM2",
        BATTERY_UUID: bytes.fromhex("12 00 00 02 03 00 01 00 05 00 00 00 00 56 00 00 00 00 01"),
        LOCATION_CAPABILITIES_UUID: bytes.fromhex("06 10 00 9c 02 00 00"),
        LOCATION_LOCK_UUID: b"\x01",
    }
    operations: list[str] = []
    updates: list[MonitorUpdate] = []
    client = StrictMonitorClient(services, values, operations)

    async def find_device(**_kwargs: object) -> ScannedDevice:
        return scanned_camera()

    async def no_sleep(_delay: float) -> None:
        return

    asyncio.run(
        stream_camera_status(
            targets=("ILCE-7CM2",),
            scan_timeout=10,
            connect_timeout=25,
            poll_interval=0.5,
            pair=True,
            on_update=updates.append,
            find_device=find_device,
            client_factory=lambda *_args, **_kwargs: client,
            sleep=no_sleep,
            max_polls=2,
        )
    )

    assert [update.phase for update in updates] == [
        MonitorPhase.SCANNING,
        MonitorPhase.CONNECTING,
        MonitorPhase.CONNECTED,
        MonitorPhase.CONNECTED,
        MonitorPhase.STOPPED,
    ]
    assert updates[-2].summary["model"] == "ILCE-7CM2"
    assert updates[-2].summary["battery_percent"] == 86
    assert updates[-2].summary["location"]["location_locked"] is True
    assert operations.count(f"read:{FIRMWARE_UUID}") == 1
    assert operations.count(f"read:{BATTERY_UUID}") == 2
    assert operations.count(f"read:{LOCATION_LOCK_UUID}") == 2
    assert operations[0] == "connect"
    assert operations[-1] == "disconnect"
