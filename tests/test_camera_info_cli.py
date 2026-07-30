import json
import re

from typer.testing import CliRunner

from sonygeotag import cli
from sonygeotag.camera_snapshot import CameraInfoSessionError
from sonygeotag.sony_info import CameraInfoSnapshot
from sonygeotag.sony_info import decode_characteristic

runner = CliRunner()
CAMERA_CONTROL_SERVICE = "8000cc00-cc00-ffff-ffff-ffffffffffff"
ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;]*m")


def sample_snapshot() -> CameraInfoSnapshot:
    characteristics = (
        decode_characteristic(
            service_uuid=CAMERA_CONTROL_SERVICE,
            uuid="0000cc0a-0000-1000-8000-00805f9b34fb",
            handle=63,
            value=b"2.01",
        ),
        decode_characteristic(
            service_uuid=CAMERA_CONTROL_SERVICE,
            uuid="0000cc0b-0000-1000-8000-00805f9b34fb",
            handle=65,
            value=b"ILCE-7CM2",
        ),
        decode_characteristic(
            service_uuid=CAMERA_CONTROL_SERVICE,
            uuid="0000cc06-0000-1000-8000-00805f9b34fb",
            handle=54,
            value=bytes.fromhex("09 00 00 54 65 73 74 2d 41 50"),
        ),
        decode_characteristic(
            service_uuid=CAMERA_CONTROL_SERVICE,
            uuid="0000cc63-0000-1000-8000-00805f9b34fb",
            handle=190,
            value=bytes.fromhex("06 00 00 00 00 00 1b"),
        ),
    )
    return CameraInfoSnapshot.create(
        captured_at="2026-07-30T05:00:00.000+00:00",
        address="00000000-1111-2222-3333-444444444444",
        name="ILCE-7CM2",
        local_name="ILCE-7CM2",
        rssi=-51,
        advertisement={"is_camera": True, "protocol_version": 101, "requires_unlock": True},
        characteristics=characteristics,
    )


def test_camera_info_help_preserves_grouped_cli_grammar() -> None:
    result = runner.invoke(cli.app, ["camera-info", "--help"])

    assert result.exit_code == 0
    help_text = ANSI_ESCAPE.sub("", result.stdout)
    assert "strict read-only" in help_text.lower()
    assert "--include-raw" in help_text
    assert "--show-sensitive" in help_text
    assert "--pair" in help_text


def test_camera_info_text_groups_summary_and_redacts_sensitive_values(monkeypatch) -> None:
    async def fake_capture(**_kwargs: object) -> CameraInfoSnapshot:
        return sample_snapshot()

    monkeypatch.setattr(cli, "capture_camera_info", fake_capture)

    result = runner.invoke(cli.app, ["camera-info", "--target", "ILCE-7CM2"])

    assert result.exit_code == 0
    assert "Identity" in result.stdout
    assert "ILCE-7CM2" in result.stdout
    assert "2.01" in result.stdout
    assert "Network" in result.stdout
    assert "[REDACTED]" in result.stdout
    assert "00000000" not in result.stdout
    assert "Test-AP" not in result.stdout
    assert "06 00 00 00 00 00 1b" not in result.stdout


def test_camera_info_json_is_schema_v1_and_requires_both_flags_for_unknown_raw(monkeypatch) -> None:
    capture_calls: list[set[str]] = []

    async def fake_capture(**kwargs: object) -> CameraInfoSnapshot:
        capture_calls.append(set(kwargs))
        return sample_snapshot()

    monkeypatch.setattr(cli, "capture_camera_info", fake_capture)

    hidden_result = runner.invoke(cli.app, ["camera-info", "--json", "--include-raw"])
    shown_result = runner.invoke(
        cli.app,
        ["camera-info", "--json", "--include-raw", "--show-sensitive"],
    )

    assert hidden_result.exit_code == 0
    assert shown_result.exit_code == 0
    hidden = json.loads(hidden_result.stdout)
    shown = json.loads(shown_result.stdout)
    assert capture_calls == [
        {"targets", "scan_timeout", "connect_timeout", "pair"},
        {"targets", "scan_timeout", "connect_timeout", "pair"},
    ]
    assert hidden["schema_version"] == 1
    assert hidden["device"]["address"] is None
    assert shown["device"]["address"] == "00000000-1111-2222-3333-444444444444"

    hidden_by_uuid = {item["uuid"]: item for item in hidden["characteristics"]}
    shown_by_uuid = {item["uuid"]: item for item in shown["characteristics"]}
    assert hidden_by_uuid["0000cc0a-0000-1000-8000-00805f9b34fb"]["raw_hex"] == "32 2e 30 31"
    assert hidden_by_uuid["0000cc06-0000-1000-8000-00805f9b34fb"]["fields"]["ssid"] is None
    assert shown_by_uuid["0000cc06-0000-1000-8000-00805f9b34fb"]["fields"]["ssid"] == "Test-AP"
    assert hidden_by_uuid["0000cc63-0000-1000-8000-00805f9b34fb"]["raw_hex"] is None
    assert shown_by_uuid["0000cc63-0000-1000-8000-00805f9b34fb"]["raw_hex"] == "06 00 00 00 00 00 1b"


def test_camera_info_returns_exit_1_when_target_is_not_found(monkeypatch) -> None:
    async def fake_capture(**_kwargs: object) -> None:
        return None

    monkeypatch.setattr(cli, "capture_camera_info", fake_capture)

    result = runner.invoke(cli.app, ["camera-info"])

    assert result.exit_code == 1
    assert "No target found" in result.stderr


def test_camera_info_returns_exit_2_for_ble_session_failure_without_traceback(monkeypatch) -> None:
    async def fake_capture(**_kwargs: object) -> None:
        raise CameraInfoSessionError("BLE camera-info session failed: radio unavailable")

    monkeypatch.setattr(cli, "capture_camera_info", fake_capture)

    result = runner.invoke(cli.app, ["camera-info"])

    assert result.exit_code == 2
    assert "BLE camera-info session failed" in result.stderr
    assert "Traceback" not in result.output
