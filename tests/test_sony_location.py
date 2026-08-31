from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from collections.abc import Callable
from dataclasses import dataclass
from typing import cast

import pytest
from bleak.backends.device import BLEDevice
from bleak.exc import BleakGATTProtocolError

from sonygeotag.ble_probe import ObservedDevice
from sonygeotag.ble_probe import ScannedDevice
from sonygeotag.sony_capabilities import CAMERA_MODEL_UUID
from sonygeotag.sony_capabilities import FIRMWARE_VERSION_UUID
from sonygeotag.sony_capabilities import GattServiceLike
from sonygeotag.sony_capabilities import SonyIdentity
from sonygeotag.sony_capabilities import descriptors_from_services
from sonygeotag.sony_capabilities import experimental_approval_key
from sonygeotag.sony_capabilities import resolve_location_profile
from sonygeotag.sony_location import _operation_error
from sonygeotag.sony_location import initialize_pairing
from sonygeotag.sony_location import sync_location
from sonygeotag.sony_protocol import CAMERA_CONTROL_SERVICE_UUID
from sonygeotag.sony_protocol import LOCATION_CONFIG_READ_UUID
from sonygeotag.sony_protocol import LOCATION_DATA_WRITE_UUID
from sonygeotag.sony_protocol import LOCATION_ENABLE_UUID
from sonygeotag.sony_protocol import LOCATION_LOCK_UUID
from sonygeotag.sony_protocol import LOCATION_SERVICE_UUID
from sonygeotag.sony_protocol import LOCATION_STATUS_NOTIFY_UUID
from sonygeotag.sony_protocol import PAIRING_INIT_UUID
from sonygeotag.sony_protocol import PAIRING_SERVICE_UUID


@dataclass
class FakeCharacteristic:
    uuid: str
    properties: tuple[str, ...]


@dataclass
class FakeService:
    uuid: str
    characteristics: tuple[FakeCharacteristic, ...]


class FakeClient:
    def __init__(
        self,
        *,
        services: tuple[FakeService, ...],
        values: dict[str, bytes],
        fail_writes: set[tuple[str, bytes]] | None = None,
        cancel_write: tuple[str, bytes] | None = None,
        hang_writes: set[tuple[str, bytes]] | None = None,
        hang_write_uuids: set[str] | None = None,
        hang_read_uuids: set[str] | None = None,
    ) -> None:
        self.services = services
        self.values = values
        self.fail_writes = fail_writes or set()
        self.cancel_write = cancel_write
        self.hang_writes = hang_writes or set()
        self.hang_write_uuids = hang_write_uuids or set()
        self.hang_read_uuids = hang_read_uuids or set()
        self.operations: list[tuple[str, str, bytes | None]] = []

    async def __aenter__(self) -> FakeClient:
        self.operations.append(("connect", "", None))
        return self

    async def __aexit__(self, *_args: object) -> None:
        self.operations.append(("disconnect", "", None))

    async def read_gatt_char(self, characteristic: object) -> bytes:
        assert isinstance(characteristic, FakeCharacteristic)
        uuid = self._uuid(characteristic)
        self.operations.append(("read", uuid, None))
        if uuid in self.hang_read_uuids:
            await asyncio.sleep(3600)
        if uuid not in self.values:
            raise OSError("not readable in this state")
        return self.values[uuid]

    async def write_gatt_char(self, characteristic: object, data: bytes, *, response: bool) -> None:
        assert isinstance(characteristic, FakeCharacteristic)
        assert response is True
        uuid = self._uuid(characteristic)
        self.operations.append(("write", uuid, data))
        if (uuid, data) == self.cancel_write:
            raise asyncio.CancelledError
        if (uuid, data) in self.hang_writes or uuid in self.hang_write_uuids:
            await asyncio.sleep(3600)
        if (uuid, data) in self.fail_writes:
            raise OSError("injected write failure")

    async def start_notify(self, characteristic: object, _callback: object) -> None:
        assert isinstance(characteristic, FakeCharacteristic)
        self.operations.append(("notify", self._uuid(characteristic), b"\x01"))

    async def stop_notify(self, characteristic: object) -> None:
        assert isinstance(characteristic, FakeCharacteristic)
        self.operations.append(("notify", self._uuid(characteristic), b"\x00"))

    @staticmethod
    def _uuid(characteristic: object) -> str:
        return characteristic.uuid if isinstance(characteristic, FakeCharacteristic) else str(characteristic)


def finder(camera: ScannedDevice) -> Callable[..., Awaitable[ScannedDevice | None]]:
    async def find_device(**_kwargs: object) -> ScannedDevice:
        return camera

    return find_device


def camera(*, model: str, version: int) -> ScannedDevice:
    observation = ObservedDevice(
        address="PRIVATE-PERIPHERAL-ID",
        name=model,
        local_name=model,
        rssi=-40,
        service_uuids=(),
        manufacturer_data={0x012D: bytes([0x03, 0x00, version, 0x00])},
    )
    return ScannedDevice(device=cast("BLEDevice", object()), observation=observation)


def services(*, modern: bool, notify: bool = True) -> tuple[FakeService, ...]:
    location = [
        FakeCharacteristic(LOCATION_DATA_WRITE_UUID, ("write",)),
        FakeCharacteristic(LOCATION_CONFIG_READ_UUID, ("read",)),
    ]
    if modern:
        location.extend(
            [
                FakeCharacteristic(LOCATION_LOCK_UUID, ("read", "write")),
                FakeCharacteristic(LOCATION_ENABLE_UUID, ("read", "write")),
            ]
        )
    if notify:
        location.append(FakeCharacteristic(LOCATION_STATUS_NOTIFY_UUID, ("notify",)))
    return (
        FakeService(
            CAMERA_CONTROL_SERVICE_UUID,
            (
                FakeCharacteristic(CAMERA_MODEL_UUID, ("read",)),
                FakeCharacteristic(FIRMWARE_VERSION_UUID, ("read",)),
            ),
        ),
        FakeService(LOCATION_SERVICE_UUID, tuple(location)),
        FakeService(PAIRING_SERVICE_UUID, (FakeCharacteristic(PAIRING_INIT_UUID, ("write",)),)),
    )


def values(model: str, firmware: str, dd21: bytes = bytes.fromhex("06 10 00 9c 02 00 00")) -> dict[str, bytes]:
    return {
        CAMERA_MODEL_UUID: model.encode(),
        FIRMWARE_VERSION_UUID: firmware.encode(),
        LOCATION_CONFIG_READ_UUID: dd21,
    }


def run_sync(
    client: FakeClient,
    *,
    model: str,
    version: int,
    allow_experimental: bool = False,
):
    firmware = client.values[FIRMWARE_VERSION_UUID].decode()
    profile = resolve_location_profile(
        protocol_version=version,
        descriptors=descriptors_from_services(cast("tuple[GattServiceLike, ...]", client.services)),
        discovery_complete=True,
    )
    approval_key = experimental_approval_key(
        SonyIdentity(model=model, firmware=firmware, protocol_version=version),
        profile,
        purpose="location-sync",
    )
    return asyncio.run(
        sync_location(
            targets=(model,),
            scan_timeout=1,
            connect_timeout=1,
            latitude=35.0,
            longitude=139.0,
            duration=0,
            interval=1,
            pair=False,
            allow_experimental=allow_experimental,
            approval_key=approval_key if allow_experimental else None,
            find_device=finder(camera(model=model, version=version)),
            client_factory=lambda *_args, **_kwargs: client,
        )
    )


def write_values(client: FakeClient) -> list[tuple[str, bytes]]:
    return [(uuid, data or b"") for operation, uuid, data in client.operations if operation == "write"]


def test_gatt_protocol_errors_preserve_only_the_sanitized_status_code() -> None:
    operation = _operation_error(
        name="write_dd30_lock",
        uuid=LOCATION_LOCK_UUID,
        direction="write",
        error=BleakGATTProtocolError(0x9D),
    )

    assert operation.error == "GATT status 0x9D"


def test_historical_a7c2_modern_flow_requires_requalification_approval_and_preserves_order() -> None:
    client = FakeClient(services=services(modern=True), values=values("ILCE-7CM2", "2.01"))

    result = run_sync(client, model="ILCE-7CM2", version=101, allow_experimental=True)

    assert result is not None and result.success
    assert [operation.name for operation in result.operations] == [
        "start_dd01_notify",
        "write_dd30_lock",
        "write_dd31_enable",
        "read_dd21_config",
        "write_dd11_location",
        "write_dd31_disable",
        "write_dd30_unlock",
        "stop_dd01_notify",
    ]
    assert len(next(data for uuid, data in write_values(client) if uuid == LOCATION_DATA_WRITE_UUID)) == 95
    assert all(uuid != PAIRING_INIT_UUID for uuid, _data in write_values(client))


def test_experimental_first_write_is_read_only_until_explicit_approval() -> None:
    client = FakeClient(services=services(modern=True), values=values("ILCE-7M4", "4.00"))

    result = run_sync(client, model="ILCE-7M4", version=101)

    assert result is not None
    assert result.approval_required is True
    assert result.packets_sent == 0
    assert write_values(client) == []
    assert not any(operation == "notify" for operation, _uuid, _data in client.operations)


def test_experimental_approval_key_cannot_authorize_a_different_identity() -> None:
    reviewed_client = FakeClient(services=services(modern=True), values=values("ILCE-7M4", "4.00"))
    reviewed = run_sync(reviewed_client, model="ILCE-7M4", version=101)
    assert reviewed is not None and reviewed.approval_key is not None

    replacement_client = FakeClient(services=services(modern=True), values=values("ILCE-6700", "2.00"))
    result = asyncio.run(
        sync_location(
            targets=("ILCE-6700",),
            scan_timeout=1,
            connect_timeout=1,
            latitude=35,
            longitude=139,
            duration=0,
            interval=1,
            allow_experimental=True,
            approval_key=reviewed.approval_key,
            find_device=finder(camera(model="ILCE-6700", version=101)),
            client_factory=lambda *_args, **_kwargs: replacement_client,
        )
    )

    assert result is not None and result.approval_required
    assert write_values(replacement_client) == []


def test_approved_legacy_flow_never_writes_controls_or_subscribes() -> None:
    client = FakeClient(services=services(modern=False), values=values("ILCE-7M3", "4.01"))

    result = run_sync(client, model="ILCE-7M3", version=64, allow_experimental=True)

    assert result is not None and result.success
    assert [operation.name for operation in result.operations] == ["read_dd21_config", "write_dd11_location"]
    writes = write_values(client)
    assert [uuid for uuid, _data in writes] == [LOCATION_DATA_WRITE_UUID]


def test_malformed_dd21_blocks_dd11_and_cleans_up_modern_controls() -> None:
    client = FakeClient(
        services=services(modern=True, notify=False),
        values=values("ILCE-7M4", "4.00", bytes.fromhex("06 10 00 9c 06 00 00")),
    )

    result = run_sync(client, model="ILCE-7M4", version=101, allow_experimental=True)

    assert result is not None and not result.success
    writes = write_values(client)
    assert writes == [
        (LOCATION_LOCK_UUID, b"\x01"),
        (LOCATION_ENABLE_UUID, b"\x01"),
        (LOCATION_ENABLE_UUID, b"\x00"),
        (LOCATION_LOCK_UUID, b"\x00"),
    ]


def test_dd31_lost_response_compensates_both_possibly_applied_controls() -> None:
    client = FakeClient(
        services=services(modern=True, notify=False),
        values=values("ILCE-7M4", "4.00"),
        fail_writes={(LOCATION_ENABLE_UUID, b"\x01")},
    )

    result = run_sync(client, model="ILCE-7M4", version=101, allow_experimental=True)

    assert result is not None and not result.success
    assert write_values(client) == [
        (LOCATION_LOCK_UUID, b"\x01"),
        (LOCATION_ENABLE_UUID, b"\x01"),
        (LOCATION_ENABLE_UUID, b"\x00"),
        (LOCATION_LOCK_UUID, b"\x00"),
    ]


def test_cancellation_after_dd30_runs_compensation_before_propagating() -> None:
    client = FakeClient(
        services=services(modern=True, notify=False),
        values=values("ILCE-7M4", "4.00"),
        cancel_write=(LOCATION_ENABLE_UUID, b"\x01"),
    )

    with pytest.raises(asyncio.CancelledError):
        run_sync(client, model="ILCE-7M4", version=101, allow_experimental=True)

    assert write_values(client) == [
        (LOCATION_LOCK_UUID, b"\x01"),
        (LOCATION_ENABLE_UUID, b"\x01"),
        (LOCATION_ENABLE_UUID, b"\x00"),
        (LOCATION_LOCK_UUID, b"\x00"),
    ]


def test_unreadable_firmware_cannot_receive_reusable_experimental_approval() -> None:
    client = FakeClient(
        services=services(modern=True, notify=False),
        values={
            CAMERA_MODEL_UUID: b"ILCE-7M4",
            LOCATION_CONFIG_READ_UUID: bytes.fromhex("06 10 00 9c 02 00 00"),
        },
    )

    result = asyncio.run(
        sync_location(
            targets=("ILCE-7M4",),
            scan_timeout=1,
            connect_timeout=1,
            latitude=35,
            longitude=139,
            duration=0,
            interval=1,
            pair=False,
            allow_experimental=True,
            approval_key="reused-key",
            find_device=finder(camera(model="ILCE-7M4", version=101)),
            client_factory=lambda *_args, **_kwargs: client,
        )
    )

    assert result is not None and result.approval_required
    assert result.approval_key is None
    assert result.operations[-1].error == "Firmware is unreadable; experimental approval is unavailable."
    assert write_values(client) == []


def test_pairing_action_requires_identity_scoped_experimental_approval() -> None:
    client = FakeClient(services=services(modern=True), values=values("ILCE-7M4", "4.00"))

    blocked = asyncio.run(
        initialize_pairing(
            targets=("ILCE-7M4",),
            scan_timeout=1,
            connect_timeout=1,
            pair=False,
            write=True,
            find_device=finder(camera(model="ILCE-7M4", version=101)),
            client_factory=lambda *_args, **_kwargs: client,
        )
    )

    assert blocked is not None
    assert blocked.approval_required is True
    assert blocked.operation.error is not None
    assert write_values(client) == []

    cross_action_client = FakeClient(services=services(modern=True), values=values("ILCE-7M4", "4.00"))
    cross_action = asyncio.run(
        sync_location(
            targets=("ILCE-7M4",),
            scan_timeout=1,
            connect_timeout=1,
            latitude=35,
            longitude=139,
            duration=0,
            interval=1,
            pair=False,
            allow_experimental=True,
            approval_key=blocked.approval_key,
            find_device=finder(camera(model="ILCE-7M4", version=101)),
            client_factory=lambda *_args, **_kwargs: cross_action_client,
        )
    )
    assert cross_action is not None and cross_action.packets_sent == 0
    assert write_values(cross_action_client) == []

    approved_client = FakeClient(services=services(modern=True), values=values("ILCE-7M4", "4.00"))
    approved = asyncio.run(
        initialize_pairing(
            targets=("ILCE-7M4",),
            scan_timeout=1,
            connect_timeout=1,
            pair=False,
            write=True,
            allow_experimental=True,
            approval_key=blocked.approval_key,
            find_device=finder(camera(model="ILCE-7M4", version=101)),
            client_factory=lambda *_args, **_kwargs: approved_client,
        )
    )

    assert approved is not None and approved.operation.error is None
    assert write_values(approved_client) == [(PAIRING_INIT_UUID, bytes.fromhex("06 08 01 00 00 00 00"))]


def test_cleanup_failure_makes_overall_session_unsuccessful() -> None:
    client = FakeClient(
        services=services(modern=True, notify=False),
        values=values("ILCE-7M4", "4.00"),
        fail_writes={(LOCATION_ENABLE_UUID, b"\x00")},
    )

    result = run_sync(client, model="ILCE-7M4", version=101, allow_experimental=True)

    assert result is not None
    assert result.write_succeeded is True
    assert result.success is False
    assert result.cleanup_diagnostic is not None


def test_dd30_timeout_still_attempts_unlock(monkeypatch) -> None:
    from sonygeotag import sony_location

    monkeypatch.setattr(sony_location, "GATT_OPERATION_TIMEOUT", 0.01)
    client = FakeClient(
        services=services(modern=True, notify=False),
        values=values("ILCE-7M4", "4.00"),
        hang_writes={(LOCATION_LOCK_UUID, b"\x01")},
    )

    result = run_sync(client, model="ILCE-7M4", version=101, allow_experimental=True)

    assert result is not None and result.write_succeeded is False
    assert write_values(client)[-1] == (LOCATION_LOCK_UUID, b"\x00")


def test_setup_timeout_triggers_partial_compensation(monkeypatch) -> None:
    from sonygeotag import sony_location

    monkeypatch.setattr(sony_location, "GATT_OPERATION_TIMEOUT", 0.01)
    client = FakeClient(
        services=services(modern=True, notify=False),
        values=values("ILCE-7M4", "4.00"),
        hang_writes={(LOCATION_ENABLE_UUID, b"\x01")},
    )

    result = run_sync(client, model="ILCE-7M4", version=101, allow_experimental=True)

    assert result is not None and result.write_succeeded is False
    assert write_values(client)[-2:] == [
        (LOCATION_ENABLE_UUID, b"\x00"),
        (LOCATION_LOCK_UUID, b"\x00"),
    ]
    assert any(operation.error == "Operation timed out." for operation in result.operations)


def test_dd21_timeout_triggers_full_compensation(monkeypatch) -> None:
    from sonygeotag import sony_location

    monkeypatch.setattr(sony_location, "GATT_OPERATION_TIMEOUT", 0.01)
    client = FakeClient(
        services=services(modern=True, notify=False),
        values=values("ILCE-7M4", "4.00"),
        hang_read_uuids={LOCATION_CONFIG_READ_UUID},
    )

    result = run_sync(client, model="ILCE-7M4", version=101, allow_experimental=True)

    assert result is not None and result.write_succeeded is False
    assert write_values(client)[-2:] == [
        (LOCATION_ENABLE_UUID, b"\x00"),
        (LOCATION_LOCK_UUID, b"\x00"),
    ]


def test_dd11_timeout_triggers_full_compensation(monkeypatch) -> None:
    from sonygeotag import sony_location

    monkeypatch.setattr(sony_location, "GATT_OPERATION_TIMEOUT", 0.01)
    client = FakeClient(
        services=services(modern=True, notify=False),
        values=values("ILCE-7M4", "4.00"),
        hang_write_uuids={LOCATION_DATA_WRITE_UUID},
    )
    result = run_sync(client, model="ILCE-7M4", version=101, allow_experimental=True)

    assert result is not None and result.write_succeeded is False
    assert write_values(client)[-2:] == [
        (LOCATION_ENABLE_UUID, b"\x00"),
        (LOCATION_LOCK_UUID, b"\x00"),
    ]


def test_cleanup_timeout_is_bounded_and_reported(monkeypatch) -> None:
    from sonygeotag import sony_location

    monkeypatch.setattr(sony_location, "CLEANUP_OPERATION_TIMEOUT", 0.01)
    client = FakeClient(
        services=services(modern=True, notify=False),
        values=values("ILCE-7M4", "4.00"),
        hang_writes={(LOCATION_ENABLE_UUID, b"\x00")},
    )

    result = run_sync(client, model="ILCE-7M4", version=101, allow_experimental=True)

    assert result is not None and result.success is False
    assert result.cleanup_diagnostic is not None
    assert "timed out" in result.cleanup_diagnostic
    assert (LOCATION_LOCK_UUID, b"\x00") in write_values(client)


def test_sanitized_result_never_exports_peripheral_id_or_dd11_coordinates() -> None:
    client = FakeClient(services=services(modern=True), values=values("ILCE-7CM2", "2.01"))
    result = run_sync(client, model="ILCE-7CM2", version=101, allow_experimental=True)

    assert result is not None
    exported = str(result.to_dict())
    assert "PRIVATE-PERIPHERAL-ID" not in exported
    assert result.to_dict()["device"]["address"] is None
    dd11 = next(operation for operation in result.to_dict()["operations"] if operation["name"] == "write_dd11_location")
    assert dd11["value_hex"] is None
