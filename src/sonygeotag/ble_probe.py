from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from bleak import BleakClient
from bleak import BleakScanner
from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData
from bleak.backends.service import BleakGATTService

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


def manufacturer_data_to_dict(manufacturer_data: dict[int, bytes]) -> dict[str, str]:
    return {f"0x{company_id:04x}": bytes_to_hex(payload) for company_id, payload in sorted(manufacturer_data.items())}


def bytes_to_hex(payload: bytes) -> str:
    return payload.hex(" ")


def normalize_targets(targets: list[str] | tuple[str, ...] | None) -> tuple[str, ...]:
    if targets is None or len(targets) == 0:
        return DEFAULT_TARGETS
    return tuple(target for target in targets if target)


def matches_targets(observation: ObservedDevice, targets: tuple[str, ...]) -> bool:
    haystack = observation.search_text().lower()
    return any(target.lower() in haystack for target in targets)


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


async def dump_gatt(targets: tuple[str, ...], scan_timeout: float, connect_timeout: float) -> GattDump | None:
    scanned = await find_target_device(targets=targets, scan_timeout=scan_timeout)
    if scanned is None:
        return None

    async with BleakClient(scanned.device, timeout=connect_timeout) as client:
        services = tuple(service_info_from_ble_service(service) for service in client.services)

    return GattDump(device=scanned.observation, services=services)


def observation_from_ble_device(device: BLEDevice, advertisement: AdvertisementData) -> ObservedDevice:
    return ObservedDevice(
        address=device.address,
        name=device.name,
        local_name=advertisement.local_name,
        rssi=advertisement.rssi,
        service_uuids=tuple(advertisement.service_uuids),
        manufacturer_data=dict(advertisement.manufacturer_data),
    )


def service_info_from_ble_service(service: BleakGATTService) -> ServiceInfo:
    characteristics = []
    for characteristic in service.characteristics:
        descriptors = tuple(
            DescriptorInfo(
                uuid=descriptor.uuid,
                handle=getattr(descriptor, "handle", None),
                description=descriptor.description,
            )
            for descriptor in characteristic.descriptors
        )
        characteristics.append(
            CharacteristicInfo(
                uuid=characteristic.uuid,
                handle=getattr(characteristic, "handle", None),
                properties=tuple(characteristic.properties),
                description=characteristic.description,
                descriptors=descriptors,
            )
        )
    return ServiceInfo(
        uuid=service.uuid,
        handle=getattr(service, "handle", None),
        description=service.description,
        characteristics=tuple(characteristics),
    )
