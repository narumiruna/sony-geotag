from __future__ import annotations

from collections.abc import Awaitable
from collections.abc import Callable
from typing import Any

from bleak import BleakClient
from bleak.exc import BleakError

from sonygeotag.ble_probe import ScannedDevice
from sonygeotag.ble_probe import find_target_device
from sonygeotag.ble_probe import utc_timestamp
from sonygeotag.sony_info import CameraInfoSnapshot
from sonygeotag.sony_info import decode_characteristic
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
        async with client_factory(scanned.device, timeout=connect_timeout, pair=pair) as client:
            for service in client.services:
                for characteristic in service.characteristics:
                    if "read" not in characteristic.properties:
                        continue
                    value: bytes | None = None
                    error_text: str | None = None
                    try:
                        value = bytes(await client.read_gatt_char(characteristic))
                    except (BleakError, TimeoutError, OSError) as error:
                        error_text = f"{type(error).__name__}: {error}"
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
        message = f"BLE camera-info session failed: {error}"
        raise CameraInfoSessionError(message) from error

    advertisement = parse_sony_advertisement(scanned.observation.manufacturer_data)
    return CameraInfoSnapshot.create(
        captured_at=timestamp_factory(),
        address=scanned.observation.address,
        name=scanned.observation.name,
        local_name=scanned.observation.local_name,
        rssi=scanned.observation.rssi,
        advertisement=advertisement.to_dict() if advertisement is not None else None,
        characteristics=tuple(decoded),
    )
