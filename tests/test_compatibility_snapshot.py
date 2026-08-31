from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from collections.abc import Callable
from dataclasses import dataclass
from typing import cast

from bleak.backends.device import BLEDevice

from sonygeotag.ble_probe import ObservedDevice
from sonygeotag.ble_probe import ScannedDevice
from sonygeotag.compatibility_snapshot import capture_compatibility_snapshot
from sonygeotag.sony_capabilities import CAMERA_MODEL_UUID
from sonygeotag.sony_capabilities import FIRMWARE_VERSION_UUID
from sonygeotag.sony_protocol import CAMERA_CONTROL_SERVICE_UUID
from sonygeotag.sony_protocol import LOCATION_CONFIG_READ_UUID
from sonygeotag.sony_protocol import LOCATION_DATA_WRITE_UUID
from sonygeotag.sony_protocol import LOCATION_ENABLE_UUID
from sonygeotag.sony_protocol import LOCATION_LOCK_UUID
from sonygeotag.sony_protocol import LOCATION_SERVICE_UUID


@dataclass
class FakeCharacteristic:
    uuid: str
    properties: tuple[str, ...]


@dataclass
class FakeService:
    uuid: str
    characteristics: tuple[FakeCharacteristic, ...]


class SnapshotClient:
    def __init__(self) -> None:
        self.services = (
            FakeService(
                CAMERA_CONTROL_SERVICE_UUID,
                (
                    FakeCharacteristic(CAMERA_MODEL_UUID, ("read",)),
                    FakeCharacteristic(FIRMWARE_VERSION_UUID, ("read",)),
                    FakeCharacteristic("0000cc07-0000-1000-8000-00805f9b34fb", ("read",)),
                ),
            ),
            FakeService(
                LOCATION_SERVICE_UUID,
                (
                    FakeCharacteristic(LOCATION_DATA_WRITE_UUID, ("write",)),
                    FakeCharacteristic(LOCATION_CONFIG_READ_UUID, ("read",)),
                    FakeCharacteristic(LOCATION_LOCK_UUID, ("write",)),
                    FakeCharacteristic(LOCATION_ENABLE_UUID, ("write",)),
                ),
            ),
        )
        self.operations: list[str] = []
        self.values = {
            CAMERA_MODEL_UUID: b"ILCE-7CM2",
            FIRMWARE_VERSION_UUID: b"2.01",
            LOCATION_CONFIG_READ_UUID: bytes.fromhex("06 10 00 9c 02 00 00"),
        }

    async def __aenter__(self) -> SnapshotClient:
        self.operations.append("connect")
        return self

    async def __aexit__(self, *_args: object) -> None:
        self.operations.append("disconnect")

    async def read_gatt_char(self, characteristic: object) -> bytes:
        assert isinstance(characteristic, FakeCharacteristic)
        self.operations.append(f"read:{characteristic.uuid}")
        return self.values[characteristic.uuid]

    async def write_gatt_char(self, *_args: object, **_kwargs: object) -> None:
        raise AssertionError("compatibility snapshot attempted a write")

    async def start_notify(self, *_args: object, **_kwargs: object) -> None:
        raise AssertionError("compatibility snapshot attempted a subscription")


def scanned_camera() -> ScannedDevice:
    return ScannedDevice(
        device=cast("BLEDevice", object()),
        observation=ObservedDevice(
            address="PRIVATE-PERIPHERAL-ID",
            name="ILCE-7CM2",
            local_name="ILCE-7CM2",
            rssi=-50,
            service_uuids=(),
            manufacturer_data={0x012D: bytes.fromhex("03 00 65 00 de ad be ef")},
        ),
    )


def finder() -> Callable[..., Awaitable[ScannedDevice | None]]:
    async def find_device(**_kwargs: object) -> ScannedDevice:
        return scanned_camera()

    return find_device


def test_compatibility_snapshot_is_strict_read_only_and_stably_sanitized() -> None:
    client = SnapshotClient()

    result = asyncio.run(
        capture_compatibility_snapshot(
            targets=("ILCE-7CM2",),
            scan_timeout=1,
            connect_timeout=1,
            find_device=finder(),
            client_factory=lambda *_args, **_kwargs: client,
            timestamp_factory=lambda: "2026-08-09T00:00:00.000+00:00",
        )
    )

    assert result is not None
    exported = result.to_dict()
    assert exported["identity"]["model"] == "ILCE-7CM2"
    assert exported["identity"]["firmware"] == "2.01"
    assert exported["profile"]["kind"] == "modern"
    assert exported["dd21"]["mode"]["packet_size"] == 95
    assert client.operations == [
        "connect",
        f"read:{CAMERA_MODEL_UUID}",
        f"read:{FIRMWARE_VERSION_UUID}",
        f"read:{LOCATION_CONFIG_READ_UUID}",
        "disconnect",
    ]
    serialized = str(exported)
    assert "PRIVATE-PERIPHERAL-ID" not in serialized
    assert "de ad be ef" not in serialized
    assert "cc07" not in serialized
