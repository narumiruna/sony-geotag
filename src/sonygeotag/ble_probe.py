from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC
from datetime import datetime
from typing import Any

from bleak import BleakClient
from bleak import BleakScanner
from bleak.backends.characteristic import BleakGATTCharacteristic
from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData
from bleak.backends.service import BleakGATTService
from bleak.exc import BleakError

DEFAULT_TARGETS = ("ILCE-7CM2", "LE_ILCE-7CM2", "7CM2")
SONY_COMPANY_ID = 0x012D


@dataclass(frozen=True)
class ObservedDevice:
    address: str
    name: str | None
    local_name: str | None
    rssi: int | None
    service_uuids: tuple[str, ...]
    manufacturer_data: dict[int, bytes]

    def search_text(self) -> str:
        manufacturer_text = " ".join(
            f"0x{company_id:04x}:{payload.hex()}" for company_id, payload in sorted(self.manufacturer_data.items())
        )
        return " ".join(
            value
            for value in (
                self.address,
                self.name or "",
                self.local_name or "",
                " ".join(self.service_uuids),
                manufacturer_text,
            )
            if value
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "address": self.address,
            "name": self.name,
            "local_name": self.local_name,
            "rssi": self.rssi,
            "service_uuids": list(self.service_uuids),
            "manufacturer_data": manufacturer_data_to_dict(self.manufacturer_data),
        }


@dataclass(frozen=True)
class ScannedDevice:
    device: BLEDevice
    observation: ObservedDevice


@dataclass(frozen=True)
class DescriptorInfo:
    uuid: str
    handle: int | None
    description: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "uuid": self.uuid,
            "handle": self.handle,
            "description": self.description,
        }


@dataclass(frozen=True)
class CharacteristicInfo:
    uuid: str
    handle: int | None
    properties: tuple[str, ...]
    description: str
    descriptors: tuple[DescriptorInfo, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "uuid": self.uuid,
            "handle": self.handle,
            "properties": list(self.properties),
            "description": self.description,
            "descriptors": [descriptor.to_dict() for descriptor in self.descriptors],
        }


@dataclass(frozen=True)
class ServiceInfo:
    uuid: str
    handle: int | None
    description: str
    characteristics: tuple[CharacteristicInfo, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "uuid": self.uuid,
            "handle": self.handle,
            "description": self.description,
            "characteristics": [characteristic.to_dict() for characteristic in self.characteristics],
        }


@dataclass(frozen=True)
class GattDump:
    device: ObservedDevice
    services: tuple[ServiceInfo, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "device": self.device.to_dict(),
            "services": [service.to_dict() for service in self.services],
        }


@dataclass(frozen=True)
class ReadValue:
    service_uuid: str
    characteristic: CharacteristicInfo
    value: bytes | None
    error: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "service_uuid": self.service_uuid,
            "characteristic": self.characteristic.to_dict(),
            "value_hex": bytes_to_hex(self.value) if self.value is not None else None,
            "value_len": len(self.value) if self.value is not None else None,
            "error": self.error,
        }


@dataclass(frozen=True)
class ReadDump:
    device: ObservedDevice
    values: tuple[ReadValue, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "device": self.device.to_dict(),
            "values": [value.to_dict() for value in self.values],
        }


@dataclass(frozen=True)
class NotificationEvent:
    timestamp: str
    uuid: str
    handle: int | None
    data: bytes

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "uuid": self.uuid,
            "handle": self.handle,
            "data_hex": bytes_to_hex(self.data),
            "data_len": len(self.data),
        }


@dataclass(frozen=True)
class NotificationSubscriptionError:
    characteristic: CharacteristicInfo
    error: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "characteristic": self.characteristic.to_dict(),
            "error": self.error,
        }


@dataclass(frozen=True)
class NotificationRun:
    device: ObservedDevice
    subscriptions: tuple[CharacteristicInfo, ...]
    subscription_errors: tuple[NotificationSubscriptionError, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "device": self.device.to_dict(),
            "subscriptions": [subscription.to_dict() for subscription in self.subscriptions],
            "subscription_errors": [error.to_dict() for error in self.subscription_errors],
        }


def manufacturer_data_to_dict(manufacturer_data: dict[int, bytes]) -> dict[str, str]:
    return {f"0x{company_id:04x}": bytes_to_hex(payload) for company_id, payload in sorted(manufacturer_data.items())}


def bytes_to_hex(payload: bytes) -> str:
    return payload.hex(" ")


def utc_timestamp() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def normalize_targets(targets: list[str] | tuple[str, ...] | None) -> tuple[str, ...]:
    if targets is None or len(targets) == 0:
        return DEFAULT_TARGETS
    return tuple(target for target in targets if target)


def normalize_characteristic_filters(filters: list[str] | tuple[str, ...] | None) -> tuple[str, ...]:
    if filters is None:
        return ()
    return tuple(characteristic_filter for characteristic_filter in filters if characteristic_filter)


def matches_targets(observation: ObservedDevice, targets: tuple[str, ...]) -> bool:
    haystack = observation.search_text().lower()
    return any(target.lower() in haystack for target in targets)


def matches_characteristic_filters(characteristic_uuid: str, filters: tuple[str, ...]) -> bool:
    if not filters:
        return True
    haystack = characteristic_uuid.lower()
    return any(characteristic_filter.lower() in haystack for characteristic_filter in filters)


async def scan_devices(scan_timeout: float) -> list[ScannedDevice]:
    discovered = await BleakScanner.discover(timeout=scan_timeout, return_adv=True)
    devices = [
        ScannedDevice(device=device, observation=observation_from_ble_device(device, advertisement))
        for device, advertisement in discovered.values()
    ]
    return sorted(
        devices,
        key=lambda scanned: scanned.observation.rssi if scanned.observation.rssi is not None else -999,
        reverse=True,
    )


async def find_target_device(targets: tuple[str, ...], scan_timeout: float) -> ScannedDevice | None:
    devices = await scan_devices(scan_timeout=scan_timeout)
    for scanned in devices:
        if matches_targets(scanned.observation, targets):
            return scanned
    return None


async def dump_gatt(
    targets: tuple[str, ...], scan_timeout: float, connect_timeout: float, pair: bool = False
) -> GattDump | None:
    scanned = await find_target_device(targets=targets, scan_timeout=scan_timeout)
    if scanned is None:
        return None

    async with BleakClient(scanned.device, timeout=connect_timeout, pair=pair) as client:
        services = tuple(service_info_from_ble_service(service) for service in client.services)

    return GattDump(device=scanned.observation, services=services)


async def read_gatt_values(
    targets: tuple[str, ...],
    scan_timeout: float,
    connect_timeout: float,
    pair: bool = False,
    characteristic_filters: tuple[str, ...] = (),
) -> ReadDump | None:
    scanned = await find_target_device(targets=targets, scan_timeout=scan_timeout)
    if scanned is None:
        return None

    values: list[ReadValue] = []
    async with BleakClient(scanned.device, timeout=connect_timeout, pair=pair) as client:
        for service in client.services:
            for characteristic in service.characteristics:
                if "read" not in characteristic.properties or not matches_characteristic_filters(
                    characteristic.uuid, characteristic_filters
                ):
                    continue
                value, error = await _read_characteristic(client=client, characteristic=characteristic)
                values.append(
                    ReadValue(
                        service_uuid=service.uuid,
                        characteristic=characteristic_info_from_ble_characteristic(characteristic),
                        value=value,
                        error=error,
                    )
                )

    return ReadDump(device=scanned.observation, values=tuple(values))


async def listen_notifications(
    targets: tuple[str, ...],
    scan_timeout: float,
    connect_timeout: float,
    listen_seconds: float,
    pair: bool,
    characteristic_filters: tuple[str, ...],
    on_event: Callable[[NotificationEvent], None],
) -> NotificationRun | None:
    scanned = await find_target_device(targets=targets, scan_timeout=scan_timeout)
    if scanned is None:
        return None

    async with BleakClient(scanned.device, timeout=connect_timeout, pair=pair) as client:
        subscriptions: list[CharacteristicInfo] = []
        subscription_errors: list[NotificationSubscriptionError] = []
        subscribed_characteristics: list[BleakGATTCharacteristic] = []
        for service in client.services:
            for characteristic in service.characteristics:
                if "notify" not in characteristic.properties or not matches_characteristic_filters(
                    characteristic.uuid, characteristic_filters
                ):
                    continue
                characteristic_info = characteristic_info_from_ble_characteristic(characteristic)
                error = await _start_notify(client=client, characteristic=characteristic, on_event=on_event)
                if error is not None:
                    subscription_errors.append(
                        NotificationSubscriptionError(characteristic=characteristic_info, error=error)
                    )
                    continue
                subscribed_characteristics.append(characteristic)
                subscriptions.append(characteristic_info)

        if subscriptions:
            await asyncio.sleep(listen_seconds)

        for characteristic in subscribed_characteristics:
            await _stop_notify(client=client, characteristic=characteristic)

    return NotificationRun(
        device=scanned.observation,
        subscriptions=tuple(subscriptions),
        subscription_errors=tuple(subscription_errors),
    )


async def _start_notify(
    client: BleakClient,
    characteristic: BleakGATTCharacteristic,
    on_event: Callable[[NotificationEvent], None],
) -> str | None:
    try:
        await client.start_notify(characteristic, lambda sender, data: on_event(notification_event(sender, data)))
    except (BleakError, TimeoutError, OSError) as error:
        return f"{type(error).__name__}: {error}"
    return None


async def _read_characteristic(
    client: BleakClient,
    characteristic: BleakGATTCharacteristic,
) -> tuple[bytes | None, str | None]:
    try:
        value = bytes(await client.read_gatt_char(characteristic))
    except (BleakError, TimeoutError, OSError) as error:
        return None, f"{type(error).__name__}: {error}"
    return value, None


async def _stop_notify(client: BleakClient, characteristic: BleakGATTCharacteristic) -> None:
    try:
        await client.stop_notify(characteristic)
    except (BleakError, TimeoutError, OSError):
        return


def observation_from_ble_device(device: BLEDevice, advertisement: AdvertisementData) -> ObservedDevice:
    return ObservedDevice(
        address=device.address,
        name=device.name,
        local_name=advertisement.local_name,
        rssi=advertisement.rssi,
        service_uuids=tuple(advertisement.service_uuids),
        manufacturer_data=dict(advertisement.manufacturer_data),
    )


def notification_event(sender: object, data: bytes | bytearray) -> NotificationEvent:
    return NotificationEvent(
        timestamp=utc_timestamp(),
        uuid=str(getattr(sender, "uuid", sender)),
        handle=_sender_handle(sender),
        data=bytes(data),
    )


def _sender_handle(sender: object) -> int | None:
    if isinstance(sender, int):
        return sender
    handle = getattr(sender, "handle", None)
    return handle if isinstance(handle, int) else None


def service_info_from_ble_service(service: BleakGATTService) -> ServiceInfo:
    return ServiceInfo(
        uuid=service.uuid,
        handle=getattr(service, "handle", None),
        description=service.description,
        characteristics=tuple(
            characteristic_info_from_ble_characteristic(characteristic) for characteristic in service.characteristics
        ),
    )


def characteristic_info_from_ble_characteristic(characteristic: BleakGATTCharacteristic) -> CharacteristicInfo:
    descriptors = tuple(
        DescriptorInfo(
            uuid=descriptor.uuid,
            handle=getattr(descriptor, "handle", None),
            description=descriptor.description,
        )
        for descriptor in characteristic.descriptors
    )
    return CharacteristicInfo(
        uuid=characteristic.uuid,
        handle=getattr(characteristic, "handle", None),
        properties=tuple(characteristic.properties),
        description=characteristic.description,
        descriptors=descriptors,
    )
