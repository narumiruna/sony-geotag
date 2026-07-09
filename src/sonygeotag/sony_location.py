from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from bleak import BleakClient
from bleak.exc import BleakError

from sonygeotag.ble_probe import NotificationEvent
from sonygeotag.ble_probe import ObservedDevice
from sonygeotag.ble_probe import bytes_to_hex
from sonygeotag.ble_probe import find_target_device
from sonygeotag.ble_probe import notification_event
from sonygeotag.sony_protocol import AREA_ADJUSTMENT_UUID
from sonygeotag.sony_protocol import LOCATION_CONFIG_READ_UUID
from sonygeotag.sony_protocol import LOCATION_DATA_WRITE_UUID
from sonygeotag.sony_protocol import LOCATION_ENABLE_UUID
from sonygeotag.sony_protocol import LOCATION_LOCK_UUID
from sonygeotag.sony_protocol import LOCATION_STATUS_NOTIFY_UUID
from sonygeotag.sony_protocol import PAIRING_INIT_UUID
from sonygeotag.sony_protocol import TIME_CORRECTION_UUID
from sonygeotag.sony_protocol import SonyAdvertisementInfo
from sonygeotag.sony_protocol import encode_location_packet
from sonygeotag.sony_protocol import encode_pairing_init
from sonygeotag.sony_protocol import parse_config_requires_timezone
from sonygeotag.sony_protocol import parse_sony_advertisement

SONY_LOCATION_READS = (
    ("read_dd32_time_correction", TIME_CORRECTION_UUID),
    ("read_dd33_area_adjustment", AREA_ADJUSTMENT_UUID),
    ("read_dd21_config", LOCATION_CONFIG_READ_UUID),
)


@dataclass(frozen=True)
class SonyGattOperation:
    name: str
    uuid: str
    direction: str
    value: bytes | None
    error: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "uuid": self.uuid,
            "direction": self.direction,
            "value_hex": bytes_to_hex(self.value) if self.value is not None else None,
            "value_len": len(self.value) if self.value is not None else None,
            "error": self.error,
        }


@dataclass(frozen=True)
class SonyLocationSyncRun:
    device: ObservedDevice
    advertisement: SonyAdvertisementInfo | None
    include_timezone: bool
    packets_sent: int
    operations: tuple[SonyGattOperation, ...]
    notifications: tuple[NotificationEvent, ...]

    @property
    def success(self) -> bool:
        return self.packets_sent > 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "device": self.device.to_dict(),
            "advertisement": self.advertisement.to_dict() if self.advertisement is not None else None,
            "include_timezone": self.include_timezone,
            "packets_sent": self.packets_sent,
            "operations": [operation.to_dict() for operation in self.operations],
            "notifications": [event.to_dict() for event in self.notifications],
        }


async def sync_location(
    targets: tuple[str, ...],
    scan_timeout: float,
    connect_timeout: float,
    latitude: float,
    longitude: float,
    duration: float,
    interval: float,
    pair: bool = False,
    vendor_pair_init: bool = False,
    include_timezone: bool | None = None,
    unlock: bool = True,
) -> SonyLocationSyncRun | None:
    scanned = await find_target_device(targets=targets, scan_timeout=scan_timeout)
    if scanned is None:
        return None

    advertisement = parse_sony_advertisement(scanned.observation.manufacturer_data)
    operations: list[SonyGattOperation] = []
    notifications: list[NotificationEvent] = []
    packets_sent = 0
    notify_started = False
    resolved_include_timezone = include_timezone if include_timezone is not None else True

    async with BleakClient(scanned.device, timeout=connect_timeout, pair=pair) as client:
        try:
            notify_operation = await _start_dd01_notifications(client=client, notifications=notifications)
            operations.append(notify_operation)
            notify_started = notify_operation.error is None

            if vendor_pair_init:
                operations.append(
                    await _write_operation(
                        client=client,
                        name="write_ee01_pairing_init",
                        uuid=PAIRING_INIT_UUID,
                        value=encode_pairing_init(),
                    )
                )

            lock_operation = await _write_operation(
                client=client,
                name="write_dd30_lock",
                uuid=LOCATION_LOCK_UUID,
                value=b"\x01",
            )
            operations.append(lock_operation)

            enable_operation = await _write_operation(
                client=client,
                name="write_dd31_enable",
                uuid=LOCATION_ENABLE_UUID,
                value=b"\x01",
            )
            operations.append(enable_operation)

            for name, uuid in SONY_LOCATION_READS:
                operations.append(await _read_operation(client=client, name=name, uuid=uuid))

            if include_timezone is None:
                resolved_include_timezone = _resolve_include_timezone(operations)

            if lock_operation.error is None and enable_operation.error is None:
                packets_sent = await _write_location_loop(
                    client=client,
                    operations=operations,
                    latitude=latitude,
                    longitude=longitude,
                    duration=duration,
                    interval=interval,
                    include_timezone=resolved_include_timezone,
                )
        finally:
            if unlock:
                operations.append(
                    await _write_operation(
                        client=client,
                        name="write_dd31_disable",
                        uuid=LOCATION_ENABLE_UUID,
                        value=b"\x00",
                    )
                )
                operations.append(
                    await _write_operation(
                        client=client,
                        name="write_dd30_unlock",
                        uuid=LOCATION_LOCK_UUID,
                        value=b"\x00",
                    )
                )
            if notify_started:
                operations.append(await _stop_dd01_notifications(client=client))

    return SonyLocationSyncRun(
        device=scanned.observation,
        advertisement=advertisement,
        include_timezone=resolved_include_timezone,
        packets_sent=packets_sent,
        operations=tuple(operations),
        notifications=tuple(notifications),
    )


def create_location_packet(
    latitude: float,
    longitude: float,
    include_timezone: bool = True,
    date_time: datetime | None = None,
) -> bytes:
    return encode_location_packet(
        latitude=latitude,
        longitude=longitude,
        date_time=date_time,
        include_timezone=include_timezone,
    )


async def _write_location_loop(
    client: BleakClient,
    operations: list[SonyGattOperation],
    latitude: float,
    longitude: float,
    duration: float,
    interval: float,
    include_timezone: bool,
) -> int:
    packets_sent = 0
    deadline = time.monotonic() + duration
    while True:
        packet = create_location_packet(
            latitude=latitude,
            longitude=longitude,
            include_timezone=include_timezone,
        )
        operation = await _write_operation(
            client=client,
            name="write_dd11_location",
            uuid=LOCATION_DATA_WRITE_UUID,
            value=packet,
        )
        operations.append(operation)
        if operation.error is None:
            packets_sent += 1

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        await asyncio.sleep(min(interval, remaining))

    return packets_sent


def _resolve_include_timezone(operations: list[SonyGattOperation]) -> bool:
    for operation in operations:
        if operation.name == "read_dd21_config" and operation.value is not None:
            return parse_config_requires_timezone(operation.value)
    return True


async def _start_dd01_notifications(
    client: BleakClient,
    notifications: list[NotificationEvent],
) -> SonyGattOperation:
    try:
        await client.start_notify(
            LOCATION_STATUS_NOTIFY_UUID,
            lambda sender, data: notifications.append(notification_event(sender, data)),
        )
    except (BleakError, TimeoutError, OSError) as error:
        return _operation_error(
            name="start_dd01_notify",
            uuid=LOCATION_STATUS_NOTIFY_UUID,
            direction="notify-start",
            error=error,
        )
    return SonyGattOperation(
        name="start_dd01_notify",
        uuid=LOCATION_STATUS_NOTIFY_UUID,
        direction="notify-start",
        value=None,
        error=None,
    )


async def _stop_dd01_notifications(client: BleakClient) -> SonyGattOperation:
    try:
        await client.stop_notify(LOCATION_STATUS_NOTIFY_UUID)
    except (BleakError, TimeoutError, OSError) as error:
        return _operation_error(
            name="stop_dd01_notify",
            uuid=LOCATION_STATUS_NOTIFY_UUID,
            direction="notify-stop",
            error=error,
        )
    return SonyGattOperation(
        name="stop_dd01_notify",
        uuid=LOCATION_STATUS_NOTIFY_UUID,
        direction="notify-stop",
        value=None,
        error=None,
    )


async def _write_operation(client: BleakClient, name: str, uuid: str, value: bytes) -> SonyGattOperation:
    try:
        await client.write_gatt_char(uuid, value, response=True)
    except (BleakError, TimeoutError, OSError) as error:
        return _operation_error(name=name, uuid=uuid, direction="write", error=error, value=value)
    return SonyGattOperation(name=name, uuid=uuid, direction="write", value=value, error=None)


async def _read_operation(client: BleakClient, name: str, uuid: str) -> SonyGattOperation:
    try:
        value = bytes(await client.read_gatt_char(uuid))
    except (BleakError, TimeoutError, OSError) as error:
        return _operation_error(name=name, uuid=uuid, direction="read", error=error)
    return SonyGattOperation(name=name, uuid=uuid, direction="read", value=value, error=None)


def _operation_error(
    name: str,
    uuid: str,
    direction: str,
    error: BaseException,
    value: bytes | None = None,
) -> SonyGattOperation:
    return SonyGattOperation(
        name=name,
        uuid=uuid,
        direction=direction,
        value=value,
        error=f"{type(error).__name__}: {error}",
    )
