from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from collections.abc import Callable
from collections.abc import Collection
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from time import monotonic
from typing import Any
from typing import Protocol
from typing import cast

from bleak import BleakClient
from bleak.exc import BleakError

from sonygeotag.ble_probe import ObservedDevice
from sonygeotag.ble_probe import ScannedDevice
from sonygeotag.ble_probe import find_target_device
from sonygeotag.ble_probe import utc_timestamp
from sonygeotag.sony_info import DecodedCharacteristic
from sonygeotag.sony_info import DecodeStatus
from sonygeotag.sony_info import decode_characteristic
from sonygeotag.sony_info import snapshot_summary

FindDevice = Callable[..., Awaitable[ScannedDevice | None]]
ClientFactory = Callable[..., Any]
Sleep = Callable[[float], Awaitable[Any]]
Clock = Callable[[], float]


class MonitorCharacteristic(Protocol):
    uuid: str
    handle: int
    properties: Collection[str]


class MonitorService(Protocol):
    uuid: str
    characteristics: Iterable[MonitorCharacteristic]


class MonitorClient(Protocol):
    services: Iterable[MonitorService]

    async def read_gatt_char(self, characteristic: MonitorCharacteristic) -> bytes | bytearray: ...


STATIC_CHARACTERISTIC_UUIDS = frozenset(
    {
        "0000cc0a-0000-1000-8000-00805f9b34fb",  # Firmware
        "0000cc0b-0000-1000-8000-00805f9b34fb",  # Model
        "0000dd21-0000-1000-8000-00805f9b34fb",  # Location capabilities
    }
)
DYNAMIC_CHARACTERISTIC_UUIDS = frozenset(
    {
        "0000cc03-0000-1000-8000-00805f9b34fb",  # Push transfer
        "0000cc09-0000-1000-8000-00805f9b34fb",  # Camera status
        "0000cc0f-0000-1000-8000-00805f9b34fb",  # Media
        "0000cc10-0000-1000-8000-00805f9b34fb",  # Battery
        "0000dd30-0000-1000-8000-00805f9b34fb",  # Location lock
        "0000dd31-0000-1000-8000-00805f9b34fb",  # Location transfer
        "0000dd32-0000-1000-8000-00805f9b34fb",  # Time correction
        "0000dd33-0000-1000-8000-00805f9b34fb",  # Area adjustment
    }
)


class MonitorPhase(StrEnum):
    SCANNING = "scanning"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    DISCONNECTED = "disconnected"
    STOPPED = "stopped"


@dataclass(frozen=True)
class MonitorUpdate:
    phase: MonitorPhase
    message: str
    captured_at: str
    device: ObservedDevice | None = None
    readings: tuple[DecodedCharacteristic, ...] = ()
    poll_count: int = 0

    @property
    def summary(self) -> dict[str, Any]:
        return snapshot_summary(self.readings)

    @property
    def read_error_count(self) -> int:
        return sum(reading.status in {DecodeStatus.UNAVAILABLE, DecodeStatus.ERROR} for reading in self.readings)


UpdateCallback = Callable[[MonitorUpdate], None]


async def stream_camera_status(
    *,
    targets: tuple[str, ...],
    scan_timeout: float,
    connect_timeout: float,
    poll_interval: float,
    pair: bool,
    on_update: UpdateCallback,
    duration: float | None = None,
    find_device: FindDevice = find_target_device,
    client_factory: ClientFactory = BleakClient,
    sleep: Sleep = asyncio.sleep,
    clock: Clock = monotonic,
    max_polls: int | None = None,
) -> None:
    """Continuously read a safe subset of camera status characteristics.

    This monitor only scans, connects, discovers services, and reads GATT values. It never
    writes characteristics or subscribes to notifications.
    """
    started_at = clock()
    poll_count = 0
    last_device: ObservedDevice | None = None
    last_readings: tuple[DecodedCharacteristic, ...] = ()

    def emit(phase: MonitorPhase, message: str) -> None:
        on_update(
            MonitorUpdate(
                phase=phase,
                message=message,
                captured_at=utc_timestamp(),
                device=last_device,
                readings=last_readings,
                poll_count=poll_count,
            )
        )

    while not _finished(started_at, duration, poll_count, max_polls, clock):
        emit(MonitorPhase.SCANNING, f"Scanning for {', '.join(targets)}")
        try:
            scanned = await find_device(targets=targets, scan_timeout=scan_timeout)
        except (BleakError, TimeoutError, OSError) as error:
            emit(MonitorPhase.DISCONNECTED, _error_message("BLE scan failed", error))
            await _retry_sleep(started_at, duration, poll_interval, sleep, clock)
            continue

        if scanned is None:
            emit(MonitorPhase.DISCONNECTED, "Camera not found; retrying")
            await _retry_sleep(started_at, duration, poll_interval, sleep, clock)
            continue

        last_device = scanned.observation
        emit(MonitorPhase.CONNECTING, f"Connecting to {scanned.observation.name or 'camera'}")
        try:
            async with client_factory(scanned.device, timeout=connect_timeout, pair=pair) as client:
                static_readings = await _read_selected(client, STATIC_CHARACTERISTIC_UUIDS)
                while not _finished(started_at, duration, poll_count, max_polls, clock):
                    if getattr(client, "is_connected", True) is False:
                        raise ConnectionError("Camera disconnected")
                    dynamic_readings = await _read_selected(client, DYNAMIC_CHARACTERISTIC_UUIDS)
                    last_readings = (*static_readings, *dynamic_readings)
                    poll_count += 1
                    emit(MonitorPhase.CONNECTED, "Read-only BLE session active")
                    if _finished(started_at, duration, poll_count, max_polls, clock):
                        break
                    await _bounded_sleep(started_at, duration, poll_interval, sleep, clock)
        except (BleakError, TimeoutError, OSError) as error:
            emit(MonitorPhase.DISCONNECTED, _error_message("BLE connection lost", error))
            await _retry_sleep(started_at, duration, poll_interval, sleep, clock)

    emit(MonitorPhase.STOPPED, "Monitor stopped")


async def _read_selected(client: object, selected_uuids: frozenset[str]) -> tuple[DecodedCharacteristic, ...]:
    readings: list[DecodedCharacteristic] = []
    typed_client = cast("MonitorClient", client)
    for service in typed_client.services:
        for characteristic in service.characteristics:
            normalized_uuid = characteristic.uuid.lower()
            if normalized_uuid not in selected_uuids or "read" not in characteristic.properties:
                continue
            value: bytes | None = None
            error_text: str | None = None
            try:
                value = bytes(await typed_client.read_gatt_char(characteristic))
            except (BleakError, TimeoutError, OSError) as error:
                error_text = f"{type(error).__name__}: {error}"
            readings.append(
                decode_characteristic(
                    service_uuid=service.uuid,
                    uuid=characteristic.uuid,
                    handle=getattr(characteristic, "handle", None),
                    value=value,
                    error=error_text,
                )
            )
    return tuple(readings)


def _finished(
    started_at: float,
    duration: float | None,
    poll_count: int,
    max_polls: int | None,
    clock: Clock,
) -> bool:
    duration_finished = duration is not None and clock() - started_at >= duration
    polls_finished = max_polls is not None and poll_count >= max_polls
    return duration_finished or polls_finished


async def _bounded_sleep(
    started_at: float,
    duration: float | None,
    delay: float,
    sleep: Sleep,
    clock: Clock,
) -> None:
    if duration is None:
        await sleep(delay)
        return
    remaining = max(0.0, duration - (clock() - started_at))
    if remaining:
        await sleep(min(delay, remaining))


async def _retry_sleep(
    started_at: float,
    duration: float | None,
    poll_interval: float,
    sleep: Sleep,
    clock: Clock,
) -> None:
    await _bounded_sleep(started_at, duration, min(poll_interval, 2.0), sleep, clock)


def _error_message(prefix: str, error: BaseException) -> str:
    return f"{prefix}: {type(error).__name__}: {error}; retrying"
