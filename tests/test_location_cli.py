from __future__ import annotations

import re
from types import SimpleNamespace

from typer.testing import CliRunner

from sonygeotag import cli
from sonygeotag.sony_location import SonyGattOperation

runner = CliRunner()
ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;]*m")


def test_real_location_cli_exposes_no_packet_or_cleanup_suppression_override() -> None:
    result = runner.invoke(cli.app, ["send-location", "--help"])

    assert result.exit_code == 0
    help_text = ANSI_ESCAPE.sub("", result.stdout)
    assert "--allow-experimental" in help_text
    assert "--approval-key" in help_text
    assert "--no-timezone" not in help_text
    assert "--no-unlock" not in help_text
    assert "--vendor-pair-init" not in help_text


def test_send_location_is_dry_run_without_write(monkeypatch) -> None:
    called = False

    async def fake_sync(**_kwargs: object) -> None:
        nonlocal called
        called = True

    monkeypatch.setattr(cli, "sync_location", fake_sync)

    result = runner.invoke(cli.app, ["send-location", "--lat", "35", "--lon", "139"])

    assert result.exit_code == 0
    assert "Dry-run only" in result.stdout
    assert called is False


def test_location_write_rejects_a_zero_length_capture_window(monkeypatch) -> None:
    called = False

    async def fake_sync(**_kwargs: object) -> None:
        nonlocal called
        called = True

    monkeypatch.setattr(cli, "sync_location", fake_sync)

    result = runner.invoke(
        cli.app,
        ["send-location", "--write", "--lat", "25", "--lon", "121", "--duration", "0"],
    )

    assert result.exit_code == 2
    assert called is False


def test_same_connection_pairing_and_location_command_is_not_exposed() -> None:
    result = runner.invoke(cli.app, ["pair-and-send-location", "--lat", "25", "--lon", "121"])

    assert result.exit_code == 2
    assert "No such command" in result.output


def test_write_commands_report_sanitized_ble_session_failures(monkeypatch) -> None:
    async def fail(**_kwargs: object) -> None:
        raise OSError("PRIVATE-PERIPHERAL-ID")

    monkeypatch.setattr(cli, "sync_location", fail)
    location_result = runner.invoke(
        cli.app,
        ["send-location", "--write", "--lat", "35", "--lon", "139"],
    )
    monkeypatch.setattr(cli, "initialize_pairing", fail)
    pairing_result = runner.invoke(cli.app, ["pair-init"])

    assert location_result.exit_code == 2
    assert "OSError" in location_result.output
    assert "PRIVATE-PERIPHERAL-ID" not in location_result.output
    assert pairing_result.exit_code == 2
    assert "OSError" in pairing_result.output
    assert "PRIVATE-PERIPHERAL-ID" not in pairing_result.output


def test_pairing_initialization_is_a_separate_dry_run_command(monkeypatch) -> None:
    calls: list[dict[str, object]] = []

    async def fake_pairing(**kwargs: object):
        calls.append(kwargs)
        return SimpleNamespace(
            identity=SimpleNamespace(model="ILCE-7CM2", firmware="2.01", protocol_version=101),
            profile=SimpleNamespace(kind=SimpleNamespace(value="modern")),
            compatibility=SimpleNamespace(confidence=SimpleNamespace(value="verified")),
            approval_key=None,
            operation=SonyGattOperation(
                name="write_ee01_pairing_init",
                uuid="0000ee01-0000-1000-8000-00805f9b34fb",
                direction="dry-run",
                value=bytes.fromhex("06 08 01 00 00 00 00"),
                error=None,
            ),
        )

    monkeypatch.setattr(cli, "initialize_pairing", fake_pairing)

    result = runner.invoke(cli.app, ["pair-init", "--target", "ILCE-7CM2"])

    assert result.exit_code == 0
    assert calls[0]["write"] is False
    assert calls[0]["allow_experimental"] is False
    assert calls[0]["approval_key"] is None
    assert "Dry-run only" in result.stdout
