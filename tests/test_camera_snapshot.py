from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from collections.abc import Callable
from dataclasses import dataclass
from typing import cast

import pytest
from bleak.backends.device import BLEDevice

from sonygeotag.ble_probe import ObservedDevice
from sonygeotag.ble_probe import ScannedDevice
from sonygeotag.camera_snapshot import CameraInfoSessionError
from sonygeotag.camera_snapshot import capture_camera_info
from sonygeotag.sony_info import DecodeStatus


@dataclass
class FakeDescriptor:
    uuid: str = "00002902-0000-1000-8000-00805f9b34fb"
    handle: int = 1
    description: str = "Fake descriptor"


@dataclass
class FakeCharacteristic:
    uuid: str
    handle: int
    properties: tuple[str, ...]
    description: str = "Vendor specific"
    descriptors: tuple[FakeDescriptor, ...] = ()


@dataclass
class FakeService:
    uuid: str
    characteristics: tuple[FakeCharacteristic, ...]


class StrictReadOnlyClient:
    def __init__(
        self,
        services: tuple[FakeService, ...],
        values: dict[str, bytes | BaseException],
        operations: list[str],
        enter_error: BaseException | None = None,
    ) -> None:
        self.services = services
        self.values = values
        self.operations = operations
        self.enter_error = enter_error

    async def __aenter__(self) -> StrictReadOnlyClient:
        self.operations.append("connect")
        if self.enter_error is not None:
            raise self.enter_error
        return self

    async def __aexit__(self, *_args: object) -> None:
        self.operations.append("disconnect")

    async def read_gatt_char(self, characteristic: FakeCharacteristic) -> bytes:
        self.operations.append(f"read:{characteristic.uuid}")
        value = self.values[characteristic.uuid]
        if isinstance(value, BaseException):
            raise value
        return value

    async def write_gatt_char(self, *_args: object, **_kwargs: object) -> None:
        raise AssertionError("strict read-only snapshot attempted write_gatt_char")

    async def start_notify(self, *_args: object, **_kwargs: object) -> None:
        raise AssertionError("strict read-only snapshot attempted start_notify")

    async def stop_notify(self, *_args: object, **_kwargs: object) -> None:
        raise AssertionError("strict read-only snapshot attempted stop_notify")


def scanned_camera() -> ScannedDevice:
    observation = ObservedDevice(
        address="00000000-1111-2222-3333-444444444444",
        name="ILCE-7CM2",
        local_name="ILCE-7CM2",
        rssi=-51,
        service_uuids=("00001800-0000-1000-8000-00805f9b34fb",),
        manufacturer_data={0x012D: bytes.fromhex("03 00 65 00")},
    )
    return ScannedDevice(device=cast("BLEDevice", object()), observation=observation)


def finder(result: ScannedDevice | None) -> Callable[..., Awaitable[ScannedDevice | None]]:
    async def find_device(**_kwargs: object) -> ScannedDevice | None:
        return result

    return find_device


def test_snapshot_reads_every_readable_characteristic_in_one_session_without_writes() -> None:
    firmware_uuid = "0000cc0a-0000-1000-8000-00805f9b34fb"
    model_uuid = "0000cc0b-0000-1000-8000-00805f9b34fb"
    write_only_uuid = "0000cc08-0000-1000-8000-00805f9b34fb"
    unavailable_uuid = "0000cc06-0000-1000-8000-00805f9b34fb"
    services = (
        FakeService(
            uuid="8000cc00-cc00-ffff-ffff-ffffffffffff",
            characteristics=(
                FakeCharacteristic(firmware_uuid, 63, ("read",)),
                FakeCharacteristic(model_uuid, 65, ("read",)),
                FakeCharacteristic(write_only_uuid, 58, ("write",)),
                FakeCharacteristic(unavailable_uuid, 54, ("read",)),
            ),
        ),
    )
    operations: list[str] = []
    client = StrictReadOnlyClient(
        services=services,
        values={
            firmware_uuid: b"2.01",
            model_uuid: b"ILCE-7CM2",
            unavailable_uuid: OSError("Application-specific Error 0x9D"),
        },
        operations=operations,
    )

    snapshot = asyncio.run(
        capture_camera_info(
            targets=("ILCE-7CM2",),
            scan_timeout=10,
            connect_timeout=25,
            pair=False,
            find_device=finder(scanned_camera()),
            client_factory=lambda *_args, **_kwargs: client,
            timestamp_factory=lambda: "2026-07-30T05:00:00.000+00:00",
        )
    )

    assert snapshot is not None
    assert snapshot.summary["model"] == "ILCE-7CM2"
    assert snapshot.summary["firmware_version"] == "2.01"
    assert len(snapshot.characteristics) == 3
    assert snapshot.characteristics[-1].status is DecodeStatus.UNAVAILABLE
    assert operations == [
        "connect",
        f"read:{firmware_uuid}",
        f"read:{model_uuid}",
        f"read:{unavailable_uuid}",
        "disconnect",
    ]


def test_snapshot_returns_none_when_target_is_not_found() -> None:
    snapshot = asyncio.run(
        capture_camera_info(
            targets=("ILCE-7CM2",),
            scan_timeout=10,
            connect_timeout=25,
            pair=False,
            find_device=finder(None),
            client_factory=lambda *_args, **_kwargs: None,
        )
    )

    assert snapshot is None


def test_snapshot_wraps_connection_failure_without_traceback_details_in_domain_result() -> None:
    client = StrictReadOnlyClient(services=(), values={}, operations=[], enter_error=OSError("radio unavailable"))

    with pytest.raises(CameraInfoSessionError, match="BLE camera-info session failed"):
        asyncio.run(
            capture_camera_info(
                targets=("ILCE-7CM2",),
                scan_timeout=10,
                connect_timeout=25,
                pair=False,
                find_device=finder(scanned_camera()),
                client_factory=lambda *_args, **_kwargs: client,
            )
        )
