from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from bleak import BleakClient
from bleak.exc import BleakError

from sonygeotag.ble_probe import ScannedDevice
from sonygeotag.ble_probe import find_target_device
from sonygeotag.ble_probe import utc_timestamp
from sonygeotag.sony_capabilities import CAMERA_MODEL_UUID
from sonygeotag.sony_capabilities import FIRMWARE_VERSION_UUID
from sonygeotag.sony_capabilities import GattServiceLike
from sonygeotag.sony_capabilities import SonyDD21Mode
from sonygeotag.sony_capabilities import SonyGattDescriptor
from sonygeotag.sony_capabilities import SonyIdentity
from sonygeotag.sony_capabilities import SonyLocationProfile
from sonygeotag.sony_capabilities import approved_snapshot_uuids
from sonygeotag.sony_capabilities import descriptors_from_services
from sonygeotag.sony_capabilities import expected_identity_service
from sonygeotag.sony_capabilities import parse_dd21_mode
from sonygeotag.sony_capabilities import resolve_compatible_profile
from sonygeotag.sony_protocol import LOCATION_CONFIG_READ_UUID
from sonygeotag.sony_protocol import parse_sony_advertisement

FindDevice = Callable[..., Awaitable[ScannedDevice | None]]
ClientFactory = Callable[..., Any]
TimestampFactory = Callable[[], str]


class CompatibilitySnapshotError(RuntimeError):
    """A strict read-only compatibility snapshot could not be completed."""


@dataclass(frozen=True)
class SonyCompatibilitySnapshot:
    schema_version: int
    captured_at: str
    identity: SonyIdentity
    profile: SonyLocationProfile
    confidence: str
    evidence: str | None
    descriptors: tuple[SonyGattDescriptor, ...]
    dd21_value_hex: str | None
    dd21_mode: SonyDD21Mode | None
    dd21_error: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "captured_at": self.captured_at,
            "identity": self.identity.to_dict(),
            "profile": self.profile.to_dict(),
            "confidence": self.confidence,
            "evidence": self.evidence,
            "descriptors": [descriptor.to_dict() for descriptor in self.descriptors],
            "dd21": {
                "value_hex": self.dd21_value_hex,
                "mode": self.dd21_mode.to_dict() if self.dd21_mode is not None else None,
                "error": self.dd21_error,
            },
        }


async def capture_compatibility_snapshot(
    *,
    targets: tuple[str, ...],
    scan_timeout: float,
    connect_timeout: float,
    pair: bool = False,
    find_device: FindDevice = find_target_device,
    client_factory: ClientFactory = BleakClient,
    timestamp_factory: TimestampFactory = utc_timestamp,
) -> SonyCompatibilitySnapshot | None:
    """Read only public model, firmware, DD21 and GATT metadata needed for capability resolution."""
    try:
        scanned = await find_device(targets=targets, scan_timeout=scan_timeout)
        if scanned is None:
            return None
        advertisement = parse_sony_advertisement(scanned.observation.manufacturer_data)
        protocol_version = advertisement.protocol_version if advertisement is not None else None
        values: dict[str, bytes | None] = {}
        errors: dict[str, str] = {}
        async with client_factory(scanned.device, timeout=connect_timeout, pair=pair) as client:
            services = tuple(client.services)
            all_descriptors = descriptors_from_services(services)
            approved_descriptors = tuple(
                descriptor
                for descriptor in all_descriptors
                if descriptor.characteristic_uuid in approved_snapshot_uuids()
                and descriptor.service_uuid == expected_identity_service(descriptor.characteristic_uuid)
            )
            approved_endpoints = _approved_endpoints(services)
            descriptor_by_uuid = {
                uuid: descriptor for uuid, (descriptor, _characteristic) in approved_endpoints.items()
            }
            for uuid in (CAMERA_MODEL_UUID, FIRMWARE_VERSION_UUID, LOCATION_CONFIG_READ_UUID.lower()):
                descriptor = descriptor_by_uuid.get(uuid)
                if descriptor is None or "read" not in descriptor.properties:
                    values[uuid] = None
                    errors[uuid] = "Approved characteristic is unavailable or not readable."
                    continue
                try:
                    values[uuid] = bytes(
                        await asyncio.wait_for(
                            client.read_gatt_char(approved_endpoints[uuid][1]),
                            timeout=connect_timeout,
                        )
                    )
                except (BleakError, TimeoutError, OSError) as error:
                    values[uuid] = None
                    errors[uuid] = _sanitized_error(error)
    except (BleakError, TimeoutError, OSError) as error:
        msg = f"BLE compatibility snapshot failed: {type(error).__name__}"
        raise CompatibilitySnapshotError(msg) from error

    model = _decode_public_ascii(values.get(CAMERA_MODEL_UUID)) or _fallback_model(scanned)
    firmware = _decode_public_ascii(values.get(FIRMWARE_VERSION_UUID))
    identity = SonyIdentity(model=model, firmware=firmware, protocol_version=protocol_version)
    profile, compatibility = resolve_compatible_profile(
        identity=identity,
        protocol_version=protocol_version,
        descriptors=all_descriptors,
    )
    dd21_value = values.get(LOCATION_CONFIG_READ_UUID.lower())
    dd21_mode: SonyDD21Mode | None = None
    dd21_error = errors.get(LOCATION_CONFIG_READ_UUID.lower())
    if dd21_value is not None:
        try:
            dd21_mode = parse_dd21_mode(dd21_value)
        except ValueError as error:
            dd21_error = str(error)

    return SonyCompatibilitySnapshot(
        schema_version=1,
        captured_at=timestamp_factory(),
        identity=identity,
        profile=profile,
        confidence=compatibility.confidence.value,
        evidence=compatibility.evidence,
        descriptors=tuple(
            sorted(
                approved_descriptors,
                key=lambda descriptor: (descriptor.service_uuid, descriptor.characteristic_uuid),
            )
        ),
        dd21_value_hex=dd21_value.hex(" ") if dd21_value is not None else None,
        dd21_mode=dd21_mode,
        dd21_error=dd21_error,
    )


def _sanitized_error(error: BaseException) -> str:
    normalized = str(error).lower()
    if "0x9d" in normalized:
        return "GATT status 0x9D"
    if "0x90" in normalized:
        return "GATT status 0x90"
    if isinstance(error, TimeoutError) or "timeout" in normalized:
        return "TimeoutError"
    if "insufficient encryption" in normalized:
        return "Insufficient encryption"
    if "insufficient authentication" in normalized:
        return "Insufficient authentication"
    return type(error).__name__


def _approved_endpoints(
    services: tuple[GattServiceLike, ...],
) -> dict[str, tuple[SonyGattDescriptor, object]]:
    endpoints: dict[str, tuple[SonyGattDescriptor, object]] = {}
    ambiguous: set[str] = set()
    for service in services:
        for characteristic in service.characteristics:
            descriptor = SonyGattDescriptor.create(
                service_uuid=str(service.uuid),
                characteristic_uuid=str(characteristic.uuid),
                properties=tuple(characteristic.properties),
            )
            if descriptor.characteristic_uuid not in approved_snapshot_uuids():
                continue
            if descriptor.service_uuid != expected_identity_service(descriptor.characteristic_uuid):
                continue
            if descriptor.characteristic_uuid in ambiguous:
                continue
            if descriptor.characteristic_uuid in endpoints:
                endpoints.pop(descriptor.characteristic_uuid)
                ambiguous.add(descriptor.characteristic_uuid)
                continue
            endpoints[descriptor.characteristic_uuid] = (descriptor, characteristic)
    return endpoints


def _decode_public_ascii(value: bytes | None) -> str | None:
    if value is None:
        return None
    try:
        decoded = value.rstrip(b"\x00").decode("ascii").strip()
    except UnicodeDecodeError:
        return None
    if not decoded or any(ord(character) < 0x20 or ord(character) > 0x7E for character in decoded):
        return None
    return decoded


def _fallback_model(scanned: ScannedDevice) -> str:
    return scanned.observation.name or scanned.observation.local_name or "UNKNOWN"
