from __future__ import annotations

import asyncio
import json
from typing import Annotated

import typer

from sonygeotag.ble_probe import GattDump
from sonygeotag.ble_probe import ScannedDevice
from sonygeotag.ble_probe import dump_gatt
from sonygeotag.ble_probe import matches_targets
from sonygeotag.ble_probe import normalize_targets
from sonygeotag.ble_probe import scan_devices

app = typer.Typer(help="Sony Alpha BLE geotag protocol probe tools.")

TimeoutOption = Annotated[float, typer.Option("--timeout", "-s", min=1.0, help="BLE scan timeout in seconds.")]
ConnectTimeoutOption = Annotated[
    float,
    typer.Option("--connect-timeout", min=1.0, help="BLE GATT connection timeout in seconds."),
]
TargetOption = Annotated[
    list[str] | None,
    typer.Option("--target", "-t", help="Target name/text to match. Repeat for multiple values."),
]
JsonOption = Annotated[bool, typer.Option("--json", help="Print machine-readable JSON.")]


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
) -> None:
    """Connect to the target camera and list GATT services/characteristics."""
    _run_gatt_dump(timeout=timeout, connect_timeout=connect_timeout, target=target, json_output=json_output)


@app.command("list-services")
def list_services(
    timeout: TimeoutOption = 10.0,
    connect_timeout: ConnectTimeoutOption = 25.0,
    target: TargetOption = None,
    json_output: JsonOption = False,
) -> None:
    """Alias for gatt-dump."""
    _run_gatt_dump(timeout=timeout, connect_timeout=connect_timeout, target=target, json_output=json_output)


def _run_gatt_dump(
    timeout: float,
    connect_timeout: float,
    target: list[str] | None,
    json_output: bool,
) -> None:
    targets = normalize_targets(target)
    result = asyncio.run(dump_gatt(targets=targets, scan_timeout=timeout, connect_timeout=connect_timeout))
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
