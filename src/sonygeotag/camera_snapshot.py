from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from collections.abc import Callable
from typing import Any

from bleak import BleakClient
from bleak.exc import BleakError

from sonygeotag.ble_probe import ScannedDevice
from sonygeotag.ble_probe import find_target_device
from sonygeotag.ble_probe import utc_timestamp
from sonygeotag.sony_capabilities import SonyGattDescriptor
from sonygeotag.sony_capabilities import SonyIdentity
from sonygeotag.sony_capabilities import descriptors_from_services
from sonygeotag.sony_capabilities import parse_dd21_mode
from sonygeotag.sony_capabilities import resolve_compatible_profile
from sonygeotag.sony_info import CameraInfoSnapshot
from sonygeotag.sony_info import decode_characteristic
from sonygeotag.sony_info import snapshot_summary
from sonygeotag.sony_protocol import LOCATION_CONFIG_READ_UUID
from sonygeotag.sony_protocol import LOCATION_SERVICE_UUID
from sonygeotag.sony_protocol import parse_sony_advertisement

FindDevice = Callable[..., Awaitable[ScannedDevice | None]]
ClientFactory = Callable[..., Any]
TimestampFactory = Callable[[], str]


class CameraInfoSessionError(RuntimeError):
    """A camera-info scan or BLE session could not be completed."""


async def capture_camera_info(
    targets: tuple[str, ...],
    scan_timeout: float,
    connect_timeout: float,
    pair: bool = False,
    *,
    find_device: FindDevice = find_target_device,
    client_factory: ClientFactory = BleakClient,
    timestamp_factory: TimestampFactory = utc_timestamp,
) -> CameraInfoSnapshot | None:
    """Capture one strict read-only Sony BLE information snapshot.

    This path only scans, connects, discovers services, reads characteristics, and disconnects.
    It deliberately has no application-level GATT write or notification-subscription operation.
    """
    try:
        scanned = await find_device(targets=targets, scan_timeout=scan_timeout)
        if scanned is None:
            return None

        decoded = []
        dd21_value: bytes | None = None
        dd21_read_error: str | None = None
        async with client_factory(scanned.device, timeout=connect_timeout, pair=pair) as client:
            descriptors = descriptors_from_services(client.services)
            for service in client.services:
                for characteristic in service.characteristics:
                    if "read" not in characteristic.properties:
                        continue
                    value: bytes | None = None
                    error_text: str | None = None
                    try:
                        value = bytes(
                            await asyncio.wait_for(
                                client.read_gatt_char(characteristic),
                                timeout=connect_timeout,
                            )
                        )
                    except (BleakError, TimeoutError, OSError) as error:
                        error_text = _sanitized_ble_error(error)
                    descriptor = SonyGattDescriptor.create(
                        service_uuid=str(service.uuid),
                        characteristic_uuid=str(characteristic.uuid),
                        properties=tuple(characteristic.properties),
                    )
                    is_dd21 = descriptor.service_uuid == LOCATION_SERVICE_UUID.lower() and (
                        descriptor.characteristic_uuid == LOCATION_CONFIG_READ_UUID.lower()
                    )
                    if is_dd21:
                        dd21_value = value if error_text is None else None
                        dd21_read_error = error_text
                    decoded.append(
                        decode_characteristic(
                            service_uuid=service.uuid,
                            uuid=characteristic.uuid,
                            handle=getattr(characteristic, "handle", None),
                            value=value,
                            error=error_text,
                        )
                    )
    except (BleakError, TimeoutError, OSError) as error:
        message = f"BLE camera-info session failed: {type(error).__name__}"
        raise CameraInfoSessionError(message) from error

    advertisement = parse_sony_advertisement(scanned.observation.manufacturer_data)
    protocol_version = advertisement.protocol_version if advertisement is not None else None
    decoded_tuple = tuple(decoded)
    location_compatibility = _location_compatibility(
        characteristics=decoded_tuple,
        descriptors=descriptors,
        device_name=scanned.observation.name or scanned.observation.local_name,
        protocol_version=protocol_version,
        dd21_value=dd21_value,
        dd21_read_error=dd21_read_error,
    )
    return CameraInfoSnapshot.create(
        captured_at=timestamp_factory(),
        address=scanned.observation.address,
        name=scanned.observation.name,
        local_name=scanned.observation.local_name,
        rssi=scanned.observation.rssi,
        advertisement=advertisement.to_dict() if advertisement is not None else None,
        characteristics=decoded_tuple,
        location_compatibility=location_compatibility,
    )


def _location_compatibility(
    *,
    characteristics: tuple[Any, ...],
    descriptors: tuple[SonyGattDescriptor, ...],
    device_name: str | None,
    protocol_version: int | None,
    dd21_value: bytes | None,
    dd21_read_error: str | None,
) -> dict[str, Any]:
    summary = snapshot_summary(characteristics)
    identity = SonyIdentity(
        model=str(summary.get("model") or device_name or "UNKNOWN"),
        firmware=str(summary["firmware_version"]) if summary.get("firmware_version") is not None else None,
        protocol_version=protocol_version,
    )
    profile, compatibility = resolve_compatible_profile(
        identity=identity,
        protocol_version=protocol_version,
        descriptors=descriptors,
    )
    dd21_mode, dd21_error = _dd21_result(descriptors, dd21_value, dd21_read_error)
    return {
        "identity": identity.to_dict(),
        "profile": profile.to_dict(),
        "confidence": compatibility.confidence.value,
        "approval_required": compatibility.confidence.value == "experimental",
        "dd21_mode": dd21_mode.to_dict() if dd21_mode is not None else None,
        "dd21_error": dd21_error,
        "cleanup_diagnostic": None,
    }


def _dd21_result(
    descriptors: tuple[SonyGattDescriptor, ...],
    value: bytes | None,
    read_error: str | None,
) -> tuple[Any, str | None]:
    descriptor = next(
        (
            item
            for item in descriptors
            if item.service_uuid == LOCATION_SERVICE_UUID.lower()
            and item.characteristic_uuid == LOCATION_CONFIG_READ_UUID.lower()
        ),
        None,
    )
    if descriptor is None:
        return None, "DD21 is missing from the Sony location service."
    if "read" not in descriptor.properties:
        return None, "DD21 is not readable."
    if value is None:
        return None, read_error or "DD21 returned no value."
    try:
        return parse_dd21_mode(value), None
    except ValueError as error:
        return None, str(error)


def _sanitized_ble_error(error: BaseException) -> str:
    normalized = str(error).lower()
    if "0x9d" in normalized:
        return "GATT status 0x9D"
    if "0x90" in normalized:
        return "GATT status 0x90"
    if "insufficient encryption" in normalized:
        return "Insufficient encryption"
    if "insufficient authentication" in normalized:
        return "Insufficient authentication"
    if isinstance(error, TimeoutError) or "timed out" in normalized or "timeout" in normalized:
        return "TimeoutError"
    return type(error).__name__
