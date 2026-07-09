from __future__ import annotations

import asyncio
import json
from typing import Annotated

import typer

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
TextOption = Annotated[bool, typer.Option("--text", help="Print human-readable text instead of JSONL.")]
PairOption = Annotated[bool, typer.Option("--pair", help="Ask Bleak/OS to pair before GATT access.")]


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


def _filters_label(filters: tuple[str, ...]) -> str:
    if not filters:
        return "<all>"
    return ", ".join(filters)
