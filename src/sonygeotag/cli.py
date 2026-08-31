from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Annotated
from typing import cast

import typer
from bleak.exc import BleakError

from sonygeotag.ble_probe import GattDump
from sonygeotag.ble_probe import NotificationEvent
from sonygeotag.ble_probe import NotificationRun
from sonygeotag.ble_probe import ReadDump
from sonygeotag.ble_probe import ScannedDevice
from sonygeotag.ble_probe import bytes_to_hex
from sonygeotag.ble_probe import dump_gatt
from sonygeotag.ble_probe import listen_notifications
from sonygeotag.ble_probe import matches_targets
from sonygeotag.ble_probe import normalize_characteristic_filters
from sonygeotag.ble_probe import normalize_targets
from sonygeotag.ble_probe import read_gatt_values
from sonygeotag.ble_probe import scan_devices
from sonygeotag.camera_snapshot import CameraInfoSessionError
from sonygeotag.camera_snapshot import capture_camera_info
from sonygeotag.compatibility_snapshot import CompatibilitySnapshotError
from sonygeotag.compatibility_snapshot import capture_compatibility_snapshot
from sonygeotag.exif_verify import ExifVerificationError
from sonygeotag.exif_verify import parse_iso_datetime
from sonygeotag.exif_verify import verify_image_exif
from sonygeotag.monitor_tui import run_camera_monitor_tui
from sonygeotag.sony_info import CameraInfoSnapshot
from sonygeotag.sony_location import SonyLocationSyncRun
from sonygeotag.sony_location import create_location_packet
from sonygeotag.sony_location import initialize_pairing
from sonygeotag.sony_location import sync_location

app = typer.Typer(help="Sony Alpha BLE geotag protocol probe tools.")

TimeoutOption = Annotated[float, typer.Option("--timeout", "-s", min=1.0, help="BLE scan timeout in seconds.")]
ConnectTimeoutOption = Annotated[
    float,
    typer.Option("--connect-timeout", min=1.0, help="BLE GATT connection timeout in seconds."),
]
DurationOption = Annotated[
    float,
    typer.Option("--duration", "-d", min=1.0, help="Notification listen duration in seconds."),
]
LocationDurationOption = Annotated[
    float,
    typer.Option(
        "--duration",
        "-d",
        min=1.0,
        help="Active location-update window in seconds; capture photos before this window closes.",
    ),
]
TargetOption = Annotated[
    list[str] | None,
    typer.Option("--target", "-t", help="Target name/text to match. Repeat for multiple values."),
]
CharacteristicOption = Annotated[
    list[str] | None,
    typer.Option(
        "--characteristic",
        "-c",
        help="Characteristic UUID/text filter. Repeat for multiple values, for example -c cc03 -c bb02.",
    ),
]
JsonOption = Annotated[bool, typer.Option("--json", help="Print machine-readable JSON.")]
IncludeRawOption = Annotated[
    bool,
    typer.Option("--include-raw", help="Include public raw hex; sensitive and unknown raw remains redacted."),
]
ShowSensitiveOption = Annotated[
    bool,
    typer.Option(
        "--show-sensitive",
        help="Reveal sensitive decoded values; pair with --include-raw to reveal sensitive or unknown raw.",
    ),
]
TextOption = Annotated[bool, typer.Option("--text", help="Print human-readable text instead of JSONL.")]
PairOption = Annotated[bool, typer.Option("--pair", help="Ask Bleak/OS to pair before GATT access.")]
ApprovalKeyOption = Annotated[
    str | None,
    typer.Option("--approval-key", help="Exact identity/profile key printed by the prior read-only attempt."),
]
NoTimezoneOption = Annotated[bool, typer.Option("--no-timezone", help="Omit DD11 timezone/DST bytes.")]


@app.command()
def scan(
    timeout: TimeoutOption = 15.0,
    target: TargetOption = None,
    json_output: JsonOption = False,
) -> None:
    """Scan BLE advertisements and highlight Sony Alpha camera matches."""
    targets = normalize_targets(target)
    devices = asyncio.run(scan_devices(scan_timeout=timeout))

    if json_output:
        typer.echo(_devices_json(devices=devices, targets=targets))
        return

    _print_scan_text(devices=devices, targets=targets)


@app.command("gatt-dump")
def gatt_dump(
    timeout: TimeoutOption = 10.0,
    connect_timeout: ConnectTimeoutOption = 25.0,
    target: TargetOption = None,
    json_output: JsonOption = False,
    pair: PairOption = False,
) -> None:
    """Connect to the target camera and list GATT services/characteristics."""
    _run_gatt_dump(timeout=timeout, connect_timeout=connect_timeout, target=target, json_output=json_output, pair=pair)


@app.command("list-services")
def list_services(
    timeout: TimeoutOption = 10.0,
    connect_timeout: ConnectTimeoutOption = 25.0,
    target: TargetOption = None,
    json_output: JsonOption = False,
    pair: PairOption = False,
) -> None:
    """Alias for gatt-dump."""
    _run_gatt_dump(timeout=timeout, connect_timeout=connect_timeout, target=target, json_output=json_output, pair=pair)


@app.command("read-values")
def read_values(
    timeout: TimeoutOption = 10.0,
    connect_timeout: ConnectTimeoutOption = 25.0,
    target: TargetOption = None,
    characteristic: CharacteristicOption = None,
    json_output: JsonOption = False,
    pair: PairOption = False,
) -> None:
    """Read all readable characteristics, optionally filtered by characteristic UUID/text."""
    targets = normalize_targets(target)
    filters = normalize_characteristic_filters(characteristic)
    result = asyncio.run(
        read_gatt_values(
            targets=targets,
            scan_timeout=timeout,
            connect_timeout=connect_timeout,
            pair=pair,
            characteristic_filters=filters,
        )
    )
    if result is None:
        typer.echo(f"No target found. Targets: {', '.join(targets)}", err=True)
        raise typer.Exit(code=1)

    if json_output:
        typer.echo(json.dumps(result.to_dict(), ensure_ascii=False, indent=2, sort_keys=True))
        return

    _print_read_dump_text(result)


@app.command("camera-info")
def camera_info(
    timeout: TimeoutOption = 10.0,
    connect_timeout: ConnectTimeoutOption = 25.0,
    target: TargetOption = None,
    json_output: JsonOption = False,
    include_raw: IncludeRawOption = False,
    show_sensitive: ShowSensitiveOption = False,
    pair: PairOption = False,
) -> None:
    """Capture a strict read-only snapshot of all readable Sony camera information."""
    targets = normalize_targets(target)
    try:
        result = asyncio.run(
            capture_camera_info(
                targets=targets,
                scan_timeout=timeout,
                connect_timeout=connect_timeout,
                pair=pair,
            )
        )
    except CameraInfoSessionError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=2) from error

    if result is None:
        typer.echo(f"No target found. Targets: {', '.join(targets)}", err=True)
        raise typer.Exit(code=1)

    if json_output:
        typer.echo(
            json.dumps(
                result.to_dict(include_raw=include_raw, show_sensitive=show_sensitive),
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
        )
        return

    _print_camera_info_text(result, include_raw=include_raw, show_sensitive=show_sensitive)


@app.command("compatibility-snapshot")
def compatibility_snapshot(
    timeout: TimeoutOption = 10.0,
    connect_timeout: ConnectTimeoutOption = 25.0,
    target: TargetOption = None,
    pair: PairOption = False,
) -> None:
    """Capture a sanitized, strict read-only Sony location compatibility snapshot."""
    targets = normalize_targets(target)
    try:
        result = asyncio.run(
            capture_compatibility_snapshot(
                targets=targets,
                scan_timeout=timeout,
                connect_timeout=connect_timeout,
                pair=pair,
            )
        )
    except CompatibilitySnapshotError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=2) from error
    if result is None:
        typer.echo(f"No target found. Targets: {', '.join(targets)}", err=True)
        raise typer.Exit(code=1)
    typer.echo(json.dumps(result.to_dict(), ensure_ascii=False, indent=2, sort_keys=True))


@app.command("verify-exif")
def verify_exif(
    photo: Annotated[Path, typer.Option("--photo", exists=True, dir_okay=False, readable=True)],
    latitude: Annotated[float, typer.Option("--lat", min=-90.0, max=90.0)],
    longitude: Annotated[float, typer.Option("--lon", min=-180.0, max=180.0)],
    not_before: Annotated[str, typer.Option("--not-before", help="ISO-8601 DD11 success timestamp with offset.")],
    camera_timezone: Annotated[
        str | None,
        typer.Option("--camera-timezone", help="IANA zone used only when image EXIF has no UTC/offset time."),
    ] = None,
) -> None:
    """Verify that a JPEG or HEIF image contains expected post-DD11 GPS EXIF."""
    try:
        result = verify_image_exif(
            photo=photo,
            expected_latitude=latitude,
            expected_longitude=longitude,
            not_before=parse_iso_datetime(not_before),
            camera_timezone=camera_timezone,
        )
    except ExifVerificationError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=2) from error
    typer.echo(json.dumps(result.to_dict(), ensure_ascii=False, indent=2, sort_keys=True))


@app.command()
def monitor(
    interval: Annotated[
        float,
        typer.Option("--interval", "-i", min=0.5, help="Seconds between read-only status polls."),
    ] = 2.0,
    duration: Annotated[
        float,
        typer.Option("--duration", "-d", min=0.0, help="Run time in seconds; 0 runs until quit."),
    ] = 0.0,
    timeout: TimeoutOption = 10.0,
    connect_timeout: ConnectTimeoutOption = 25.0,
    target: TargetOption = None,
    pair: PairOption = False,
) -> None:
    """Open a live, read-only Textual dashboard for camera status."""
    run_camera_monitor_tui(
        targets=normalize_targets(target),
        scan_timeout=timeout,
        connect_timeout=connect_timeout,
        poll_interval=interval,
        pair=pair,
        duration=duration or None,
    )


@app.command("notify-log")
def notify_log(
    duration: DurationOption = 30.0,
    timeout: TimeoutOption = 10.0,
    connect_timeout: ConnectTimeoutOption = 25.0,
    target: TargetOption = None,
    characteristic: CharacteristicOption = None,
    text: TextOption = False,
    pair: PairOption = False,
) -> None:
    """Subscribe to notify characteristics and stream notification packets as JSONL."""
    targets = normalize_targets(target)
    filters = normalize_characteristic_filters(characteristic)
    typer.echo(
        f"Listening for {duration:g}s. Targets: {', '.join(targets)}. "
        f"Characteristic filters: {_filters_label(filters)}",
        err=True,
    )

    result = asyncio.run(
        listen_notifications(
            targets=targets,
            scan_timeout=timeout,
            connect_timeout=connect_timeout,
            listen_seconds=duration,
            pair=pair,
            characteristic_filters=filters,
            on_event=lambda event: _print_notification_event(event=event, text=text),
        )
    )
    if result is None:
        typer.echo(f"No target found. Targets: {', '.join(targets)}", err=True)
        raise typer.Exit(code=1)
    if not result.subscriptions:
        _print_notification_summary(result=result, duration=duration)
        typer.echo("No notify characteristics subscribed successfully.", err=True)
        raise typer.Exit(code=2)

    _print_notification_summary(result=result, duration=duration)


@app.command("encode-location")
def encode_location(
    latitude: Annotated[float, typer.Option("--lat", min=-90.0, max=90.0, help="Latitude in degrees.")],
    longitude: Annotated[float, typer.Option("--lon", min=-180.0, max=180.0, help="Longitude in degrees.")],
    no_timezone: NoTimezoneOption = False,
) -> None:
    """Encode a Sony DD11 location packet without touching BLE."""
    packet = create_location_packet(latitude=latitude, longitude=longitude, include_timezone=not no_timezone)
    typer.echo(bytes_to_hex(packet))


@app.command("send-location")
def send_location(
    latitude: Annotated[float, typer.Option("--lat", min=-90.0, max=90.0, help="Latitude in degrees.")],
    longitude: Annotated[float, typer.Option("--lon", min=-180.0, max=180.0, help="Longitude in degrees.")],
    duration: LocationDurationOption = 60.0,
    interval: Annotated[float, typer.Option("--interval", "-i", min=1.0, help="Seconds between DD11 writes.")] = 30.0,
    timeout: TimeoutOption = 10.0,
    connect_timeout: ConnectTimeoutOption = 30.0,
    target: TargetOption = None,
    json_output: JsonOption = False,
    pair: PairOption = False,
    write: Annotated[bool, typer.Option("--write", help="Actually write to the camera. Omit for dry-run.")] = False,
    allow_experimental: Annotated[
        bool,
        typer.Option(
            "--allow-experimental",
            help="Approve only the identity/profile matching --approval-key for this invocation.",
        ),
    ] = False,
    approval_key: ApprovalKeyOption = None,
) -> None:
    """Send GPS using the capability-resolved Sony modern or legacy location flow."""
    if not write:
        packet = create_location_packet(latitude=latitude, longitude=longitude, include_timezone=True)
        typer.echo("Dry-run only; add --write to touch the camera.")
        typer.echo(bytes_to_hex(packet))
        return

    targets = normalize_targets(target)
    try:
        result = asyncio.run(
            sync_location(
                targets=targets,
                scan_timeout=timeout,
                connect_timeout=connect_timeout,
                latitude=latitude,
                longitude=longitude,
                duration=duration,
                interval=interval,
                pair=pair,
                allow_experimental=allow_experimental,
                approval_key=approval_key,
            )
        )
    except (BleakError, TimeoutError, OSError) as error:
        typer.echo(f"Sony location session failed: {type(error).__name__}", err=True)
        raise typer.Exit(code=2) from error
    if result is None:
        typer.echo(f"No target found. Targets: {', '.join(targets)}", err=True)
        raise typer.Exit(code=1)

    if json_output:
        typer.echo(json.dumps(result.to_dict(), ensure_ascii=False, indent=2, sort_keys=True))
    else:
        _print_location_sync_text(result)

    if not result.success:
        raise typer.Exit(code=3)


@app.command("pair-init")
def pair_init(
    timeout: TimeoutOption = 10.0,
    connect_timeout: ConnectTimeoutOption = 30.0,
    target: TargetOption = None,
    pair: PairOption = False,
    write: Annotated[bool, typer.Option("--write", help="Actually send EE01. Omit for dry-run.")] = False,
    allow_experimental: Annotated[
        bool,
        typer.Option("--allow-experimental", help="Approve only the identity/profile matching --approval-key."),
    ] = False,
    approval_key: ApprovalKeyOption = None,
) -> None:
    """Run Sony EE01 pairing initialization as a separate explicit action."""
    targets = normalize_targets(target)
    try:
        result = asyncio.run(
            initialize_pairing(
                targets=targets,
                scan_timeout=timeout,
                connect_timeout=connect_timeout,
                pair=pair,
                write=write,
                allow_experimental=allow_experimental,
                approval_key=approval_key,
            )
        )
    except (BleakError, TimeoutError, OSError) as error:
        typer.echo(f"Sony pairing session failed: {type(error).__name__}", err=True)
        raise typer.Exit(code=2) from error
    if result is None:
        typer.echo(f"No target found. Targets: {', '.join(targets)}", err=True)
        raise typer.Exit(code=1)
    typer.echo(
        f"Identity: model={result.identity.model} firmware={result.identity.firmware or 'unknown'} "
        f"protocol={result.identity.protocol_version if result.identity.protocol_version is not None else 'unknown'} "
        f"profile={result.profile.kind.value} confidence={result.compatibility.confidence.value}"
    )
    if result.approval_key is not None:
        typer.echo(f"Approval key: {result.approval_key}")
    if not write:
        typer.echo("Dry-run only; add --write to send Sony EE01 pairing initialization.")
    operation = result.operation
    status = "OK" if operation.error is None else f"ERROR {operation.error}"
    typer.echo(f"{operation.name} {operation.direction} {operation.uuid} len={len(operation.value or b'')} {status}")
    if operation.error is not None:
        raise typer.Exit(code=3)


def _run_gatt_dump(
    timeout: float,
    connect_timeout: float,
    target: list[str] | None,
    json_output: bool,
    pair: bool,
) -> None:
    targets = normalize_targets(target)
    result = asyncio.run(dump_gatt(targets=targets, scan_timeout=timeout, connect_timeout=connect_timeout, pair=pair))
    if result is None:
        typer.echo(f"No target found. Targets: {', '.join(targets)}", err=True)
        raise typer.Exit(code=1)

    if json_output:
        typer.echo(json.dumps(result.to_dict(), ensure_ascii=False, indent=2, sort_keys=True))
        return

    _print_gatt_text(result)


def _devices_json(devices: list[ScannedDevice], targets: tuple[str, ...]) -> str:
    payload = {
        "targets": list(targets),
        "devices": [
            {
                **scanned.observation.to_dict(),
                "matched": matches_targets(scanned.observation, targets),
            }
            for scanned in devices
        ],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)


def _print_scan_text(devices: list[ScannedDevice], targets: tuple[str, ...]) -> None:
    matches = [scanned for scanned in devices if matches_targets(scanned.observation, targets)]
    typer.echo(f"Found {len(devices)} BLE address(es). Targets: {', '.join(targets)}")

    if matches:
        typer.echo("\nMatches:")
        for scanned in matches:
            _print_device(scanned)
    else:
        typer.echo("\nNo target match found.")

    named_devices = [scanned for scanned in devices if scanned.observation.name or scanned.observation.local_name]
    if named_devices:
        typer.echo("\nNamed devices seen:")
        for scanned in named_devices:
            _print_device(scanned)


def _print_device(scanned: ScannedDevice) -> None:
    observation = scanned.observation
    name = observation.name or observation.local_name or "<unnamed>"
    typer.echo(f"- rssi={observation.rssi} address={observation.address} name={name!r}")
    if observation.service_uuids:
        typer.echo(f"  services={list(observation.service_uuids)}")
    if observation.manufacturer_data:
        typer.echo(f"  manufacturer_data={observation.to_dict()['manufacturer_data']}")


def _print_gatt_text(result: GattDump) -> None:
    device = result.device
    typer.echo(f"Device: {device.name or device.local_name or '<unnamed>'} address={device.address} rssi={device.rssi}")
    for service in result.services:
        typer.echo(f"SERVICE {service.uuid} handle={service.handle} description={service.description}")
        for characteristic in service.characteristics:
            typer.echo(
                f"  CHAR {characteristic.uuid} handle={characteristic.handle} "
                f"props={list(characteristic.properties)} description={characteristic.description}"
            )
            for descriptor in characteristic.descriptors:
                typer.echo(
                    f"    DESC {descriptor.uuid} handle={descriptor.handle} description={descriptor.description}"
                )


def _print_read_dump_text(result: ReadDump) -> None:
    device = result.device
    typer.echo(f"Device: {device.name or device.local_name or '<unnamed>'} address={device.address} rssi={device.rssi}")
    if not result.values:
        typer.echo("No readable characteristics matched.")
        return

    for value in result.values:
        characteristic = value.characteristic
        if value.error is None:
            payload = bytes_to_hex(value.value or b"")
            value_len = len(value.value or b"")
            typer.echo(f"READ {characteristic.uuid} handle={characteristic.handle} len={value_len} value={payload}")
        else:
            typer.echo(f"READ {characteristic.uuid} handle={characteristic.handle} ERROR {value.error}")


def _print_camera_info_text(
    result: CameraInfoSnapshot,
    *,
    include_raw: bool,
    show_sensitive: bool,
) -> None:
    device = result.device.to_dict(show_sensitive=show_sensitive)
    address = device["address"] if device["address"] is not None else "[REDACTED]"
    name = result.device.name or result.device.local_name or "<unnamed>"
    typer.echo("Strict read-only Sony camera information snapshot")
    typer.echo(f"Captured: {result.captured_at}")
    typer.echo(f"Device: name={name!r} address={address} rssi={result.device.rssi}")
    _print_camera_advertisement(result.advertisement)
    _print_location_compatibility(result.location_compatibility)

    category_labels = {
        "identity": "Identity",
        "battery": "Battery",
        "storage": "Storage",
        "camera_status": "Camera Status",
        "network": "Network",
        "location": "Location",
        "pairing": "Pairing",
        "camera_control": "Unknown Camera Control Protocol",
        "remote": "Remote Protocol",
        "protocol": "Unknown Protocol",
        "unknown": "Unknown Protocol",
    }
    category_order = tuple(category_labels)
    grouped = {
        category: [item for item in result.characteristics if item.category == category] for category in category_order
    }
    extra_categories = sorted({item.category for item in result.characteristics} - set(category_order))
    for category in (*category_order, *extra_categories):
        items = grouped.get(category) or [item for item in result.characteristics if item.category == category]
        if not items:
            continue
        typer.echo(f"\n{category_labels.get(category, category.replace('_', ' ').title())}:")
        for item in items:
            serialized = item.to_dict(include_raw=include_raw, show_sensitive=show_sensitive)
            status = serialized["status"]
            confidence = serialized["confidence"]
            if serialized["redacted"]:
                fields_text = "[REDACTED]"
            elif serialized["fields"]:
                fields_text = json.dumps(serialized["fields"], ensure_ascii=False, sort_keys=True)
            else:
                fields_text = "{}"
            typer.echo(f"- {item.name} ({item.uuid}) status={status} confidence={confidence} fields={fields_text}")
            if serialized["raw_hex"] is not None:
                typer.echo(f"  raw={serialized['raw_hex']}")
            if item.warning is not None:
                typer.echo(f"  warning={item.warning}")
            if item.error is not None:
                typer.echo(f"  error={item.error}")

    counts = result.to_dict(include_raw=False, show_sensitive=False)["counts"]
    typer.echo("\nResults: " + ", ".join(f"{status}={count}" for status, count in counts.items()))


def _print_camera_advertisement(advertisement: dict[str, bool | int | None] | None) -> None:
    if advertisement is None:
        return
    typer.echo(
        "Advertisement: "
        f"camera={advertisement.get('is_camera')} "
        f"protocol_version={advertisement.get('protocol_version')} "
        f"requires_unlock={advertisement.get('requires_unlock')}"
    )


def _print_location_compatibility(compatibility: dict[str, object] | None) -> None:
    if compatibility is None:
        return
    identity = compatibility["identity"]
    profile = compatibility["profile"]
    dd21_mode = compatibility["dd21_mode"]
    if not isinstance(identity, dict) or not isinstance(profile, dict):
        return
    identity_data = cast(dict[str, object], identity)
    profile_data = cast(dict[str, object], profile)
    dd21_data = cast(dict[str, object], dd21_mode) if isinstance(dd21_mode, dict) else {}
    packet_size = dd21_data.get("packet_size", "unknown")
    typer.echo(
        "Location compatibility: "
        f"model={identity_data.get('model')} firmware={identity_data.get('firmware') or 'unknown'} "
        f"profile={profile_data.get('kind')} confidence={compatibility.get('confidence')} "
        f"approval_required={compatibility.get('approval_required')} packet_size={packet_size}"
    )
    if compatibility.get("dd21_error") is not None:
        typer.echo(f"DD21 negotiation: {compatibility['dd21_error']}")


def _print_notification_event(event: NotificationEvent, text: bool) -> None:
    if text:
        payload = bytes_to_hex(event.data)
        typer.echo(f"{event.timestamp} {event.uuid} handle={event.handle} len={len(event.data)} data={payload}")
        return
    typer.echo(json.dumps(event.to_dict(), ensure_ascii=False, sort_keys=True))


def _print_notification_summary(result: NotificationRun, duration: float) -> None:
    typer.echo(
        f"Subscribed to {len(result.subscriptions)} notify characteristic(s); listened {duration:g}s.",
        err=True,
    )
    for subscription in result.subscriptions:
        typer.echo(f"- {subscription.uuid} handle={subscription.handle}", err=True)
    if result.subscription_errors:
        typer.echo(f"Failed subscriptions: {len(result.subscription_errors)}", err=True)
        for subscription_error in result.subscription_errors:
            characteristic = subscription_error.characteristic
            typer.echo(f"- {characteristic.uuid} handle={characteristic.handle}: {subscription_error.error}", err=True)


def _print_location_sync_text(result: SonyLocationSyncRun) -> None:
    device = result.device
    typer.echo(f"Device: {device.name or device.local_name or '<unnamed>'} rssi={device.rssi}")
    if result.advertisement is not None:
        typer.echo(
            "Sony advertisement: "
            f"camera={result.advertisement.is_camera} "
            f"protocol_version={result.advertisement.protocol_version} "
            f"requires_unlock={result.advertisement.requires_unlock}"
        )
    typer.echo(
        f"Identity: model={result.identity.model} firmware={result.identity.firmware or 'unknown'} "
        f"profile={result.profile.kind.value} confidence={result.compatibility.confidence.value}"
    )
    typer.echo(f"Reason: {result.profile.reason}")
    typer.echo(f"Approval required: {result.approval_required}")
    if result.approval_key is not None:
        typer.echo(f"Approval key: {result.approval_key}")
    typer.echo(f"Location sync: success={result.success} packets_sent={result.packets_sent}")
    if result.dd21_mode is not None:
        typer.echo(
            f"DD21: mode={'timezone' if result.dd21_mode.include_timezone else 'compact'} "
            f"packet_size={result.dd21_mode.packet_size}"
        )
    if result.cleanup_diagnostic is not None:
        typer.echo(result.cleanup_diagnostic)

    typer.echo("Operations:")
    for operation in result.operations:
        status = "OK" if operation.error is None else f"ERROR {operation.error}"
        value_len = len(operation.value) if operation.value is not None else 0
        typer.echo(f"- {operation.name} {operation.direction} {operation.uuid} len={value_len} {status}")

    if result.notifications:
        typer.echo("DD01 notifications:")
        for event in result.notifications:
            typer.echo(f"- {event.timestamp} len={len(event.data)} data=[REDACTED]")


def _filters_label(filters: tuple[str, ...]) -> str:
    if not filters:
        return "<all>"
    return ", ".join(filters)
