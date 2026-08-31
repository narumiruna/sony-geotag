from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable
from collections.abc import Callable
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from typing import Protocol

from bleak import BleakClient
from bleak.exc import BleakError
from bleak.exc import BleakGATTProtocolError

from sonygeotag.ble_probe import NotificationEvent
from sonygeotag.ble_probe import ObservedDevice
from sonygeotag.ble_probe import ScannedDevice
from sonygeotag.ble_probe import bytes_to_hex
from sonygeotag.ble_probe import find_target_device
from sonygeotag.ble_probe import notification_event
from sonygeotag.sony_capabilities import CAMERA_MODEL_UUID
from sonygeotag.sony_capabilities import FIRMWARE_VERSION_UUID
from sonygeotag.sony_capabilities import GattServiceLike
from sonygeotag.sony_capabilities import SonyCompatibilityEntry
from sonygeotag.sony_capabilities import SonyDD21Mode
from sonygeotag.sony_capabilities import SonyGattDescriptor
from sonygeotag.sony_capabilities import SonyIdentity
from sonygeotag.sony_capabilities import SonyLocationProfile
from sonygeotag.sony_capabilities import SonySupportConfidence
from sonygeotag.sony_capabilities import descriptors_from_services
from sonygeotag.sony_capabilities import experimental_approval_key
from sonygeotag.sony_capabilities import parse_dd21_mode
from sonygeotag.sony_capabilities import resolve_compatible_profile
from sonygeotag.sony_protocol import CAMERA_CONTROL_SERVICE_UUID
from sonygeotag.sony_protocol import LOCATION_DATA_WRITE_UUID
from sonygeotag.sony_protocol import LOCATION_SERVICE_UUID
from sonygeotag.sony_protocol import LOCATION_STATUS_NOTIFY_UUID
from sonygeotag.sony_protocol import PAIRING_INIT_UUID
from sonygeotag.sony_protocol import PAIRING_SERVICE_UUID
from sonygeotag.sony_protocol import SonyAdvertisementInfo
from sonygeotag.sony_protocol import encode_location_packet
from sonygeotag.sony_protocol import encode_pairing_init
from sonygeotag.sony_protocol import parse_sony_advertisement
from sonygeotag.sony_session import SonySessionAction
from sonygeotag.sony_session import SonySessionActionKind
from sonygeotag.sony_session import compensation_actions
from sonygeotag.sony_session import create_session_plan


class SonyBLEClient(Protocol):
    services: Iterable[GattServiceLike]

    async def read_gatt_char(self, characteristic: object) -> bytes | bytearray: ...

    async def write_gatt_char(self, characteristic: object, data: bytes, *, response: bool) -> None: ...

    async def start_notify(self, characteristic: object, callback: Callable[[object, bytearray], None]) -> None: ...

    async def stop_notify(self, characteristic: object) -> None: ...


FindDevice = Callable[..., Awaitable[ScannedDevice | None]]
ClientFactory = Callable[..., Any]
GATT_OPERATION_TIMEOUT = 12.0
CLEANUP_OPERATION_TIMEOUT = 12.0


@dataclass(frozen=True)
class SonyGattOperation:
    name: str
    uuid: str
    direction: str
    value: bytes | None
    error: str | None

    def to_dict(self) -> dict[str, Any]:
        public_value = self.value if self.name == "read_dd21_config" else None
        return {
            "name": self.name,
            "uuid": self.uuid,
            "direction": self.direction,
            "value_hex": bytes_to_hex(public_value) if public_value is not None else None,
            "value_len": len(self.value) if self.value is not None else None,
            "error": self.error,
        }


@dataclass(frozen=True)
class SonyPairingInitRun:
    identity: SonyIdentity
    profile: SonyLocationProfile
    compatibility: SonyCompatibilityEntry
    approval_required: bool
    approval_key: str | None
    operation: SonyGattOperation


@dataclass(frozen=True)
class SonyLocationSyncRun:
    device: ObservedDevice
    identity: SonyIdentity
    advertisement: SonyAdvertisementInfo | None
    profile: SonyLocationProfile
    compatibility: SonyCompatibilityEntry
    dd21_mode: SonyDD21Mode | None
    packets_sent: int
    operations: tuple[SonyGattOperation, ...]
    notifications: tuple[NotificationEvent, ...]
    approval_required: bool
    cleanup_diagnostic: str | None
    approval_key: str | None

    @property
    def write_succeeded(self) -> bool:
        return self.packets_sent > 0

    @property
    def success(self) -> bool:
        return self.write_succeeded and self.cleanup_diagnostic is None

    @property
    def include_timezone(self) -> bool:
        return self.dd21_mode.include_timezone if self.dd21_mode is not None else True

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "write_succeeded": self.write_succeeded,
            "device": {
                "address": None,
                "name": self.device.name,
                "local_name": self.device.local_name,
                "rssi": self.device.rssi,
                "service_uuids": list(self.device.service_uuids),
                "manufacturer_data": {},
                "address_redacted": True,
            },
            "identity": self.identity.to_dict(),
            "advertisement": self.advertisement.to_dict() if self.advertisement is not None else None,
            "profile": self.profile.to_dict(),
            "confidence": self.compatibility.confidence.value,
            "evidence": self.compatibility.evidence,
            "approval_required": self.approval_required,
            "approval_key": self.approval_key,
            "dd21_mode": self.dd21_mode.to_dict() if self.dd21_mode is not None else None,
            "include_timezone": self.include_timezone,
            "packets_sent": self.packets_sent,
            "operations": [operation.to_dict() for operation in self.operations],
            "notifications": [
                {
                    "timestamp": event.timestamp,
                    "uuid": event.uuid,
                    "data_len": len(event.data),
                    "data_hex": None,
                }
                for event in self.notifications
            ],
            "cleanup_diagnostic": self.cleanup_diagnostic,
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
    allow_experimental: bool = False,
    approval_key: str | None = None,
    *,
    find_device: FindDevice = find_target_device,
    client_factory: ClientFactory = BleakClient,
) -> SonyLocationSyncRun | None:
    scanned = await find_device(targets=targets, scan_timeout=scan_timeout)
    if scanned is None:
        return None

    advertisement = parse_sony_advertisement(scanned.observation.manufacturer_data)
    protocol_version = advertisement.protocol_version if advertisement is not None else None
    operations: list[SonyGattOperation] = []
    notifications: list[NotificationEvent] = []
    packets_sent = 0
    dd21_mode: SonyDD21Mode | None = None
    cleanup_diagnostic: str | None = None

    async with client_factory(scanned.device, timeout=connect_timeout, pair=pair) as client:
        services = tuple(client.services)
        descriptors = descriptors_from_services(services)
        identity_endpoints = _endpoints_for_service(services, CAMERA_CONTROL_SERVICE_UUID)
        location_endpoints = _endpoints_for_service(services, LOCATION_SERVICE_UUID)
        model = await _read_identity_value(client, identity_endpoints.get(CAMERA_MODEL_UUID.lower()))
        model = model or _fallback_model(scanned.observation)
        firmware = await _read_identity_value(client, identity_endpoints.get(FIRMWARE_VERSION_UUID.lower()))
        identity = SonyIdentity(model=model, firmware=firmware, protocol_version=protocol_version)
        profile, compatibility = resolve_compatible_profile(
            identity=identity,
            protocol_version=protocol_version,
            descriptors=descriptors,
        )
        approval_required = compatibility.confidence is SonySupportConfidence.EXPERIMENTAL
        required_approval_key = (
            experimental_approval_key(identity, profile, purpose="location-sync")
            if approval_required and identity.firmware is not None
            else None
        )
        approval_matches = (
            allow_experimental and required_approval_key is not None and approval_key == required_approval_key
        )
        if approval_required and identity.firmware is None:
            operations.append(
                SonyGattOperation(
                    name="experimental_approval",
                    uuid=FIRMWARE_VERSION_UUID,
                    direction="guard",
                    value=None,
                    error="Firmware is unreadable; experimental approval is unavailable.",
                )
            )

        if not profile.executable or (approval_required and not approval_matches):
            return _run_result(
                scanned=scanned,
                identity=identity,
                advertisement=advertisement,
                profile=profile,
                compatibility=compatibility,
                dd21_mode=None,
                packets_sent=0,
                operations=operations,
                notifications=notifications,
                approval_required=approval_required,
                cleanup_diagnostic=None,
                approval_key=required_approval_key,
            )

        outcome = await _execute_location_session(
            client=client,
            endpoints=location_endpoints,
            profile=profile,
            operations=operations,
            notifications=notifications,
            latitude=latitude,
            longitude=longitude,
            duration=duration,
            interval=interval,
        )
        packets_sent = outcome.packets_sent
        dd21_mode = outcome.dd21_mode
        cleanup_diagnostic = outcome.cleanup_diagnostic

    return _run_result(
        scanned=scanned,
        identity=identity,
        advertisement=advertisement,
        profile=profile,
        compatibility=compatibility,
        dd21_mode=dd21_mode,
        packets_sent=packets_sent,
        operations=operations,
        notifications=notifications,
        approval_required=approval_required,
        cleanup_diagnostic=cleanup_diagnostic,
        approval_key=required_approval_key,
    )


async def initialize_pairing(
    targets: tuple[str, ...],
    scan_timeout: float,
    connect_timeout: float,
    pair: bool,
    write: bool,
    allow_experimental: bool = False,
    approval_key: str | None = None,
    *,
    find_device: FindDevice = find_target_device,
    client_factory: ClientFactory = BleakClient,
) -> SonyPairingInitRun | None:
    """Run Sony EE01 as a separate, identity-gated action; dry-run unless write is true."""
    scanned = await find_device(targets=targets, scan_timeout=scan_timeout)
    if scanned is None:
        return None
    advertisement = parse_sony_advertisement(scanned.observation.manufacturer_data)
    protocol_version = advertisement.protocol_version if advertisement is not None else None
    async with client_factory(scanned.device, timeout=connect_timeout, pair=pair) as client:
        services = tuple(client.services)
        descriptors = descriptors_from_services(services)
        identity_endpoints = _endpoints_for_service(services, CAMERA_CONTROL_SERVICE_UUID)
        pairing_endpoints = _endpoints_for_service(services, PAIRING_SERVICE_UUID)
        identity = SonyIdentity(
            model=await _read_identity_value(client, identity_endpoints.get(CAMERA_MODEL_UUID.lower()))
            or _fallback_model(scanned.observation),
            firmware=await _read_identity_value(client, identity_endpoints.get(FIRMWARE_VERSION_UUID.lower())),
            protocol_version=protocol_version,
        )
        profile, compatibility = resolve_compatible_profile(
            identity=identity,
            protocol_version=protocol_version,
            descriptors=descriptors,
        )
        approval_required = compatibility.confidence is SonySupportConfidence.EXPERIMENTAL
        required_approval_key = (
            experimental_approval_key(identity, profile, purpose="pair-init")
            if approval_required and identity.firmware is not None
            else None
        )
        descriptor = next(
            (
                item
                for item in descriptors
                if item.service_uuid == PAIRING_SERVICE_UUID.lower()
                and item.characteristic_uuid == PAIRING_INIT_UUID.lower()
            ),
            None,
        )
        error: str | None = None
        if descriptor is None or "write" not in descriptor.properties:
            error = "EE01 write-with-response is unavailable in the Sony pairing service."
        elif not profile.executable or compatibility.confidence is SonySupportConfidence.UNSUPPORTED:
            error = "The detected compatibility profile blocks pairing initialization."
        elif write and approval_required and required_approval_key is None:
            error = "Firmware is unreadable; experimental pairing approval is unavailable."
        elif write and approval_required and not (allow_experimental and approval_key == required_approval_key):
            error = (
                "Experimental approval is required for this exact identity/profile before EE01; "
                f"approval key: {required_approval_key}."
            )

        if not write or error is not None:
            operation = SonyGattOperation(
                name="write_ee01_pairing_init",
                uuid=PAIRING_INIT_UUID,
                direction="dry-run" if not write else "read-only",
                value=encode_pairing_init(),
                error=error,
            )
        else:
            try:
                operation = await asyncio.wait_for(
                    _write_operation(
                        client=client,
                        characteristic=pairing_endpoints[PAIRING_INIT_UUID.lower()],
                        name="write_ee01_pairing_init",
                        uuid=PAIRING_INIT_UUID,
                        value=encode_pairing_init(),
                    ),
                    timeout=GATT_OPERATION_TIMEOUT,
                )
            except TimeoutError:
                operation = SonyGattOperation(
                    name="write_ee01_pairing_init",
                    uuid=PAIRING_INIT_UUID,
                    direction="write",
                    value=encode_pairing_init(),
                    error="Operation timed out.",
                )
    return SonyPairingInitRun(
        identity=identity,
        profile=profile,
        compatibility=compatibility,
        approval_required=approval_required,
        approval_key=required_approval_key,
        operation=operation,
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


@dataclass
class _SetupState:
    dd21_mode: SonyDD21Mode | None = None
    notify_may_be_started: bool = False
    dd30_may_be_acquired: bool = False
    dd31_may_be_acquired: bool = False
    dd30_confirmed: bool = False
    dd31_confirmed: bool = False


@dataclass(frozen=True)
class _SessionOutcome:
    dd21_mode: SonyDD21Mode | None
    packets_sent: int
    cleanup_diagnostic: str | None


async def _execute_location_session(
    *,
    client: SonyBLEClient,
    endpoints: dict[str, object],
    profile: SonyLocationProfile,
    operations: list[SonyGattOperation],
    notifications: list[NotificationEvent],
    latitude: float,
    longitude: float,
    duration: float,
    interval: float,
) -> _SessionOutcome:
    state = _SetupState()
    packets_sent = 0
    cleanup_diagnostic: str | None = None
    try:
        await _run_setup(
            client=client,
            endpoints=endpoints,
            profile=profile,
            operations=operations,
            notifications=notifications,
            state=state,
        )
        if state.dd21_mode is not None and _required_setup_succeeded(
            profile,
            state.dd30_confirmed,
            state.dd31_confirmed,
        ):
            packets_sent = await _write_location_loop(
                client=client,
                characteristic=endpoints[LOCATION_DATA_WRITE_UUID.lower()],
                operations=operations,
                latitude=latitude,
                longitude=longitude,
                duration=duration,
                interval=interval,
                include_timezone=state.dd21_mode.include_timezone,
            )
    finally:
        cleanup_diagnostic = await _run_cleanup_protected(
            client=client,
            endpoints=endpoints,
            operations=operations,
            notifications=notifications,
            state=state,
        )
    return _SessionOutcome(state.dd21_mode, packets_sent, cleanup_diagnostic)


async def _run_setup(
    *,
    client: SonyBLEClient,
    endpoints: dict[str, object],
    profile: SonyLocationProfile,
    operations: list[SonyGattOperation],
    notifications: list[NotificationEvent],
    state: _SetupState,
) -> None:
    for action in create_session_plan(profile).setup:
        state.notify_may_be_started = state.notify_may_be_started or action.name == "start_dd01_notify"
        state.dd30_may_be_acquired = state.dd30_may_be_acquired or action.name == "write_dd30_lock"
        state.dd31_may_be_acquired = state.dd31_may_be_acquired or action.name == "write_dd31_enable"
        operation = await _perform_action(client, endpoints, action, notifications)
        operations.append(operation)
        if operation.error is not None and action.required:
            break
        if operation.error is not None:
            continue
        state.dd30_confirmed = state.dd30_confirmed or action.name == "write_dd30_lock"
        state.dd31_confirmed = state.dd31_confirmed or action.name == "write_dd31_enable"
        if action.name == "read_dd21_config" and operation.value is not None:
            try:
                state.dd21_mode = parse_dd21_mode(operation.value)
            except ValueError as error:
                operations[-1] = SonyGattOperation(
                    name=operation.name,
                    uuid=operation.uuid,
                    direction=operation.direction,
                    value=operation.value,
                    error=str(error),
                )
                break


async def _run_cleanup_protected(
    *,
    client: SonyBLEClient,
    endpoints: dict[str, object],
    operations: list[SonyGattOperation],
    notifications: list[NotificationEvent],
    state: _SetupState,
) -> str | None:
    cleanup_task = asyncio.create_task(
        _run_cleanup(
            client=client,
            endpoints=endpoints,
            operations=operations,
            notifications=notifications,
            state=state,
        )
    )
    try:
        return await asyncio.shield(cleanup_task)
    except asyncio.CancelledError:
        await cleanup_task
        raise


async def _run_cleanup(
    *,
    client: SonyBLEClient,
    endpoints: dict[str, object],
    operations: list[SonyGattOperation],
    notifications: list[NotificationEvent],
    state: _SetupState,
) -> str | None:
    errors: list[str] = []
    actions = compensation_actions(
        dd30_acquired=state.dd30_may_be_acquired,
        dd31_acquired=state.dd31_may_be_acquired,
    )
    for action in actions:
        try:
            operation = await asyncio.wait_for(
                _perform_action(client, endpoints, action, notifications),
                timeout=CLEANUP_OPERATION_TIMEOUT,
            )
        except TimeoutError:
            operation = SonyGattOperation(
                name=action.name,
                uuid=action.uuid,
                direction=action.kind.value,
                value=action.value,
                error="Cleanup operation timed out.",
            )
        operations.append(operation)
        if operation.error is not None:
            errors.append(f"{action.name}: {operation.error}")
    if state.notify_may_be_started:
        operation = await _bounded_stop_notifications(client, endpoints)
        operations.append(operation)
        if operation.error is not None:
            errors.append(f"stop_dd01_notify: {operation.error}")
    return "Incomplete cleanup: " + "; ".join(errors) if errors else None


async def _bounded_stop_notifications(
    client: SonyBLEClient,
    endpoints: dict[str, object],
) -> SonyGattOperation:
    characteristic = endpoints.get(LOCATION_STATUS_NOTIFY_UUID.lower())
    if characteristic is None:
        return SonyGattOperation(
            name="stop_dd01_notify",
            uuid=LOCATION_STATUS_NOTIFY_UUID,
            direction="notify-stop",
            value=None,
            error="Expected service-owned characteristic is unavailable.",
        )
    try:
        return await asyncio.wait_for(
            _stop_dd01_notifications(client, characteristic),
            timeout=CLEANUP_OPERATION_TIMEOUT,
        )
    except TimeoutError:
        return SonyGattOperation(
            name="stop_dd01_notify",
            uuid=LOCATION_STATUS_NOTIFY_UUID,
            direction="notify-stop",
            value=None,
            error="Cleanup operation timed out.",
        )


async def _read_identity_value(client: SonyBLEClient, characteristic: object | None) -> str | None:
    if characteristic is None:
        return None
    try:
        value = bytes(
            await asyncio.wait_for(
                client.read_gatt_char(characteristic),
                timeout=GATT_OPERATION_TIMEOUT,
            )
        )
    except (BleakError, TimeoutError, OSError):
        return None
    try:
        decoded = value.rstrip(b"\x00").decode("ascii").strip()
    except UnicodeDecodeError:
        return None
    if not decoded or any(ord(character) < 0x20 or ord(character) > 0x7E for character in decoded):
        return None
    return decoded


def _endpoints_for_service(
    services: tuple[GattServiceLike, ...],
    service_uuid: str,
) -> dict[str, object]:
    expected_service = service_uuid.lower()
    endpoints: dict[str, object] = {}
    for service in services:
        if str(service.uuid).lower() != expected_service:
            continue
        for characteristic in service.characteristics:
            descriptor = SonyGattDescriptor.create(
                service_uuid=str(service.uuid),
                characteristic_uuid=str(characteristic.uuid),
                properties=tuple(characteristic.properties),
            )
            endpoints[descriptor.characteristic_uuid] = characteristic
    return endpoints


def _fallback_model(device: ObservedDevice) -> str:
    return device.name or device.local_name or "UNKNOWN"


def _required_setup_succeeded(
    profile: SonyLocationProfile,
    dd30_acquired: bool,
    dd31_acquired: bool,
) -> bool:
    if profile.kind.value == "legacy":
        return True
    return dd30_acquired and dd31_acquired


async def _perform_action(
    client: SonyBLEClient,
    endpoints: dict[str, object],
    action: SonySessionAction,
    notifications: list[NotificationEvent],
) -> SonyGattOperation:
    try:
        return await asyncio.wait_for(
            _perform_action_unbounded(client, endpoints, action, notifications),
            timeout=GATT_OPERATION_TIMEOUT,
        )
    except TimeoutError:
        return SonyGattOperation(
            name=action.name,
            uuid=action.uuid,
            direction=action.kind.value,
            value=action.value,
            error="Operation timed out.",
        )


async def _perform_action_unbounded(
    client: SonyBLEClient,
    endpoints: dict[str, object],
    action: SonySessionAction,
    notifications: list[NotificationEvent],
) -> SonyGattOperation:
    characteristic = endpoints.get(action.uuid.lower())
    if characteristic is None:
        return SonyGattOperation(
            name=action.name,
            uuid=action.uuid,
            direction=action.kind.value,
            value=action.value,
            error="Expected service-owned characteristic is unavailable.",
        )
    if action.kind is SonySessionActionKind.WRITE:
        return await _write_operation(client, characteristic, action.name, action.uuid, action.value or b"")
    if action.kind is SonySessionActionKind.READ:
        return await _read_operation(client, characteristic, action.name, action.uuid)
    return await _start_dd01_notifications(
        client=client,
        characteristic=characteristic,
        notifications=notifications,
    )


async def _write_location_loop(
    client: SonyBLEClient,
    characteristic: object,
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
        try:
            operation = await asyncio.wait_for(
                _write_operation(
                    client=client,
                    characteristic=characteristic,
                    name="write_dd11_location",
                    uuid=LOCATION_DATA_WRITE_UUID,
                    value=packet,
                ),
                timeout=GATT_OPERATION_TIMEOUT,
            )
        except TimeoutError:
            operation = SonyGattOperation(
                name="write_dd11_location",
                uuid=LOCATION_DATA_WRITE_UUID,
                direction="write",
                value=packet,
                error="Operation timed out.",
            )
        operations.append(operation)
        if operation.error is not None:
            break
        packets_sent += 1
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        await asyncio.sleep(min(interval, remaining))
    return packets_sent


async def _start_dd01_notifications(
    client: SonyBLEClient,
    characteristic: object,
    notifications: list[NotificationEvent],
) -> SonyGattOperation:
    try:
        await client.start_notify(
            characteristic,
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


async def _stop_dd01_notifications(
    client: SonyBLEClient,
    characteristic: object,
) -> SonyGattOperation:
    try:
        await client.stop_notify(characteristic)
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


async def _write_operation(
    client: SonyBLEClient,
    characteristic: object,
    name: str,
    uuid: str,
    value: bytes,
) -> SonyGattOperation:
    try:
        await client.write_gatt_char(characteristic, value, response=True)
    except (BleakError, TimeoutError, OSError) as error:
        return _operation_error(name=name, uuid=uuid, direction="write", error=error, value=value)
    return SonyGattOperation(name=name, uuid=uuid, direction="write", value=value, error=None)


async def _read_operation(
    client: SonyBLEClient,
    characteristic: object,
    name: str,
    uuid: str,
) -> SonyGattOperation:
    try:
        value = bytes(await client.read_gatt_char(characteristic))
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
        error=_sanitized_operation_error(error),
    )


def _sanitized_operation_error(error: BaseException) -> str:
    if isinstance(error, BleakGATTProtocolError):
        return f"GATT status 0x{int(error.code):02X}"
    if isinstance(error, TimeoutError):
        return "TimeoutError"
    return type(error).__name__


def _run_result(
    *,
    scanned: ScannedDevice,
    identity: SonyIdentity,
    advertisement: SonyAdvertisementInfo | None,
    profile: SonyLocationProfile,
    compatibility: SonyCompatibilityEntry,
    dd21_mode: SonyDD21Mode | None,
    packets_sent: int,
    operations: list[SonyGattOperation],
    notifications: list[NotificationEvent],
    approval_required: bool,
    cleanup_diagnostic: str | None,
    approval_key: str | None,
) -> SonyLocationSyncRun:
    return SonyLocationSyncRun(
        device=scanned.observation,
        identity=identity,
        advertisement=advertisement,
        profile=profile,
        compatibility=compatibility,
        dd21_mode=dd21_mode,
        packets_sent=packets_sent,
        operations=tuple(operations),
        notifications=tuple(notifications),
        approval_required=approval_required,
        cleanup_diagnostic=cleanup_diagnostic,
        approval_key=approval_key,
    )
