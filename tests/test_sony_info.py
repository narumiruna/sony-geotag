import pytest

from sonygeotag import sony_info
from sonygeotag.sony_info import CameraInfoSnapshot
from sonygeotag.sony_info import CharacteristicSpec
from sonygeotag.sony_info import Confidence
from sonygeotag.sony_info import DecodeStatus
from sonygeotag.sony_info import ParseResult
from sonygeotag.sony_info import Sensitivity
from sonygeotag.sony_info import decode_characteristic
from sonygeotag.sony_info import snapshot_summary

CAMERA_CONTROL_SERVICE = "8000cc00-cc00-ffff-ffff-ffffffffffff"
LOCATION_SERVICE = "8000dd00-dd00-ffff-ffff-ffffffffffff"


def test_decodes_verified_firmware_and_model_payloads() -> None:
    firmware = decode_characteristic(
        service_uuid=CAMERA_CONTROL_SERVICE,
        uuid="0000cc0a-0000-1000-8000-00805f9b34fb",
        handle=63,
        value=bytes.fromhex("32 2e 30 31"),
    )
    model = decode_characteristic(
        service_uuid=CAMERA_CONTROL_SERVICE,
        uuid="0000cc0b-0000-1000-8000-00805f9b34fb",
        handle=65,
        value=bytes.fromhex("49 4c 43 45 2d 37 43 4d 32"),
    )
    framed_model = decode_characteristic(
        service_uuid=CAMERA_CONTROL_SERVICE,
        uuid="0000cc11-0000-1000-8000-00805f9b34fb",
        handle=80,
        value=bytes.fromhex("0b 00 00 49 4c 43 45 2d 37 43 4d 32"),
    )

    assert firmware.status is DecodeStatus.DECODED
    assert firmware.confidence is Confidence.VERIFIED
    assert firmware.fields == {"firmware_version": "2.01"}
    assert model.fields == {"model": "ILCE-7CM2"}
    assert framed_model.fields == {"model": "ILCE-7CM2"}
    assert snapshot_summary((firmware, model, framed_model))["model"] == "ILCE-7CM2"


def test_empty_or_control_character_ascii_is_partial() -> None:
    empty = decode_characteristic(
        service_uuid=CAMERA_CONTROL_SERVICE,
        uuid="0000cc0a-0000-1000-8000-00805f9b34fb",
        handle=63,
        value=b"",
    )
    control = decode_characteristic(
        service_uuid=CAMERA_CONTROL_SERVICE,
        uuid="0000cc0a-0000-1000-8000-00805f9b34fb",
        handle=63,
        value=b"2.\x010",
    )

    assert empty.status is DecodeStatus.PARTIAL
    assert empty.fields == {"firmware_version": None}
    assert control.status is DecodeStatus.PARTIAL
    assert control.fields == {"firmware_version": None}


def test_invalid_ascii_is_partial_instead_of_raising() -> None:
    result = decode_characteristic(
        service_uuid=CAMERA_CONTROL_SERVICE,
        uuid="0000cc0a-0000-1000-8000-00805f9b34fb",
        handle=63,
        value=b"\xff\xfe",
    )

    assert result.status is DecodeStatus.PARTIAL
    assert result.fields == {"firmware_version": None}
    assert "ASCII" in (result.warning or "")


def test_cc03_decodes_current_push_transfer_state() -> None:
    idle = decode_characteristic(
        service_uuid=CAMERA_CONTROL_SERVICE,
        uuid="0000cc03-0000-1000-8000-00805f9b34fb",
        handle=51,
        value=bytes.fromhex("03 00 00 01"),
    )
    ready = decode_characteristic(
        service_uuid=CAMERA_CONTROL_SERVICE,
        uuid="0000cc03-0000-1000-8000-00805f9b34fb",
        handle=51,
        value=bytes.fromhex("03 00 00 02"),
    )

    assert idle.fields == {"push_transfer_code": 1, "push_transfer_state": "idle"}
    assert ready.fields == {"push_transfer_code": 2, "push_transfer_state": "ready"}


def test_cc09_decodes_known_tags_and_retains_unknown_tags() -> None:
    payload = bytes.fromhex(
        "04 00 01 00 00 "
        "03 00 02 01 "
        "03 00 03 01 "
        "05 00 04 00 00 00 "
        "03 00 05 01 "
        "03 00 06 01 "
        "03 00 07 00 "
        "03 00 08 00 "
        "03 00 09 00 "
        "03 00 0a 01"
    )

    result = decode_characteristic(
        service_uuid=CAMERA_CONTROL_SERVICE,
        uuid="0000cc09-0000-1000-8000-00805f9b34fb",
        handle=60,
        value=payload,
    )

    assert result.status is DecodeStatus.PARTIAL
    assert result.fields["wifi_state"] == "terminated"
    assert result.fields["image_transfer_available"] is True
    assert result.fields["remote_control_available"] is True
    assert result.fields["time_setting_complete"] is True
    assert result.fields["live_streaming"] is False
    assert result.fields["movie_recording"] is False
    assert result.fields["streaming_mode"] is False
    assert result.fields["background_transfer_available"] is True
    assert result.fields["unknown_tags"] == {"0x0004": [0], "0x0006": [1]}


def test_cc09_truncated_and_duplicate_tlv_is_partial() -> None:
    result = decode_characteristic(
        service_uuid=CAMERA_CONTROL_SERVICE,
        uuid="0000cc09-0000-1000-8000-00805f9b34fb",
        handle=60,
        value=bytes.fromhex("03 00 02 01 03 00 02 00 05 00"),
    )

    assert result.status is DecodeStatus.PARTIAL
    assert result.fields["image_transfer_available"] is True
    assert result.fields["duplicate_tags"] == ["0x0002"]
    assert "truncated" in (result.warning or "").lower()


def test_cc10_decodes_observed_battery_percentage_and_power_state() -> None:
    result = decode_characteristic(
        service_uuid=CAMERA_CONTROL_SERVICE,
        uuid="0000cc10-0000-1000-8000-00805f9b34fb",
        handle=72,
        value=bytes.fromhex("12 00 00 02 03 00 01 00 05 00 00 00 00 56 00 00 00 00 01"),
    )

    assert result.status is DecodeStatus.DECODED
    assert result.fields["batteries"] == [
        {"slot_flags": 1, "position_code": 0, "level_code": 5, "reserved_code": 0, "remaining_percent": 86}
    ]
    assert result.fields["external_power_code"] == 1
    assert result.fields["external_power_state"] == "not_powered"


def test_cc10_decodes_second_observed_battery_level_layout() -> None:
    result = decode_characteristic(
        service_uuid=CAMERA_CONTROL_SERVICE,
        uuid="0000cc10-0000-1000-8000-00805f9b34fb",
        handle=72,
        value=bytes.fromhex("12 00 00 02 03 00 01 00 04 00 00 00 00 4d 00 00 00 00 01"),
    )

    assert result.status is DecodeStatus.DECODED
    assert result.fields["batteries"] == [
        {"slot_flags": 1, "position_code": 0, "level_code": 4, "reserved_code": 0, "remaining_percent": 77}
    ]
    assert result.fields["external_power_state"] == "not_powered"


def test_cc10_truncated_pack_is_partial() -> None:
    result = decode_characteristic(
        service_uuid=CAMERA_CONTROL_SERVICE,
        uuid="0000cc10-0000-1000-8000-00805f9b34fb",
        handle=72,
        value=bytes.fromhex("09 00 00 02 03 00 01 00 05 00"),
    )

    assert result.status is DecodeStatus.PARTIAL
    assert "truncated" in (result.warning or "").lower()


def test_cc0f_decodes_evidence_backed_primary_media_fields_and_keeps_trailing_data() -> None:
    result = decode_characteristic(
        service_uuid=CAMERA_CONTROL_SERVICE,
        uuid="0000cc0f-0000-1000-8000-00805f9b34fb",
        handle=69,
        value=bytes.fromhex("17 00 00 02 03 01 01 00 00 00 08 7d 00 00 00 00 00 00 00 00 00 00 00 00"),
    )

    assert result.status is DecodeStatus.PARTIAL
    assert result.fields["protocol_version"] == 2
    assert result.fields["primary_slot"]["status"] == "present"
    assert result.fields["primary_slot"]["remaining_shots"] == 2173
    assert result.fields["primary_slot"]["remaining_recording_seconds"] == 0
    assert result.fields["unparsed_trailing_bytes"] == 8


def test_location_characteristics_decode_capabilities_and_flags() -> None:
    feature = decode_characteristic(
        service_uuid=LOCATION_SERVICE,
        uuid="0000dd21-0000-1000-8000-00805f9b34fb",
        handle=242,
        value=bytes.fromhex("06 10 00 9c 02 00 00"),
    )
    lock = decode_characteristic(
        service_uuid=LOCATION_SERVICE,
        uuid="0000dd30-0000-1000-8000-00805f9b34fb",
        handle=244,
        value=b"\x00",
    )
    enabled = decode_characteristic(
        service_uuid=LOCATION_SERVICE,
        uuid="0000dd31-0000-1000-8000-00805f9b34fb",
        handle=246,
        value=b"\x01",
    )

    assert feature.fields["timezone_supported"] is True
    assert feature.fields["location_packet_size"] == 95
    assert lock.fields == {"location_locked": False}
    assert enabled.fields == {"location_transfer_enabled": True}


def test_location_boolean_rejects_non_boolean_code() -> None:
    result = decode_characteristic(
        service_uuid=LOCATION_SERVICE,
        uuid="0000dd30-0000-1000-8000-00805f9b34fb",
        handle=244,
        value=b"\x02",
    )

    assert result.status is DecodeStatus.PARTIAL
    assert result.fields == {"location_locked": None, "location_locked_code": 2}


def test_sensitive_values_and_unknown_raw_are_redacted_by_default() -> None:
    ssid = decode_characteristic(
        service_uuid=CAMERA_CONTROL_SERVICE,
        uuid="0000cc06-0000-1000-8000-00805f9b34fb",
        handle=54,
        value=bytes.fromhex("09 00 00 54 65 73 74 2d 41 50"),
    )
    unknown = decode_characteristic(
        service_uuid=CAMERA_CONTROL_SERVICE,
        uuid="0000cc63-0000-1000-8000-00805f9b34fb",
        handle=190,
        value=bytes.fromhex("06 00 00 00 00 00 1b"),
    )

    assert ssid.sensitivity is Sensitivity.NETWORK
    assert ssid.to_dict(include_raw=True, show_sensitive=False)["fields"] == {"ssid": None}
    assert ssid.to_dict(include_raw=True, show_sensitive=False)["raw_hex"] is None
    assert ssid.to_dict(include_raw=False, show_sensitive=True)["fields"] == {"ssid": "Test-AP"}
    assert ssid.to_dict(include_raw=False, show_sensitive=True)["raw_hex"] is None

    hidden_unknown = unknown.to_dict(include_raw=True, show_sensitive=False)
    shown_unknown = unknown.to_dict(include_raw=True, show_sensitive=True)
    assert unknown.status is DecodeStatus.UNKNOWN
    assert hidden_unknown["raw_hex"] is None
    assert hidden_unknown["redacted"] is True
    assert shown_unknown["raw_hex"] == "06 00 00 00 00 00 1b"


def test_synthetic_network_and_ftp_payloads_decode_but_remain_sensitive() -> None:
    password = decode_characteristic(
        service_uuid=CAMERA_CONTROL_SERVICE,
        uuid="0000cc07-0000-1000-8000-00805f9b34fb",
        handle=56,
        value=bytes.fromhex("0b 00 00") + b"secret123",
    )
    bssid = decode_characteristic(
        service_uuid=CAMERA_CONTROL_SERVICE,
        uuid="0000cc0c-0000-1000-8000-00805f9b34fb",
        handle=67,
        value=b"AA:BB:CC:DD:EE:FF",
    )
    ftp = decode_characteristic(
        service_uuid=CAMERA_CONTROL_SERVICE,
        uuid="0000cc40-0000-1000-8000-00805f9b34fb",
        handle=171,
        value=bytes.fromhex("0e 00 00") + b"Studio\nRoad\n",
    )

    assert password.fields == {"password": "secret123"}
    assert password.sensitivity is Sensitivity.SECRET
    assert bssid.fields == {"bssid": "AA:BB:CC:DD:EE:FF"}
    assert ftp.fields == {"ftp_profile_names": ["Studio", "Road"]}
    assert password.to_dict(include_raw=False, show_sensitive=False)["fields"] == {"password": None}
    assert bssid.to_dict(include_raw=False, show_sensitive=False)["fields"] == {"bssid": None}
    assert ftp.to_dict(include_raw=False, show_sensitive=False)["fields"] == {"ftp_profile_names": None}


def test_battery_usb_power_and_media_absence_use_sanitized_fixtures() -> None:
    battery = decode_characteristic(
        service_uuid=CAMERA_CONTROL_SERVICE,
        uuid="0000cc10-0000-1000-8000-00805f9b34fb",
        handle=72,
        value=bytes.fromhex("12 00 00 02 03 00 01 00 05 00 00 00 00 64 00 00 00 00 03"),
    )
    media = decode_characteristic(
        service_uuid=CAMERA_CONTROL_SERVICE,
        uuid="0000cc0f-0000-1000-8000-00805f9b34fb",
        handle=69,
        value=bytes.fromhex("0f 00 00 02 01 01 00 00 00 00 00 00 00 00 00 00"),
    )

    assert battery.fields["batteries"] == [
        {"slot_flags": 1, "position_code": 0, "level_code": 5, "reserved_code": 0, "remaining_percent": 100}
    ]
    assert battery.fields["external_power_state"] == "usb_power"
    assert media.fields["primary_slot"]["status"] == "absent"
    assert media.fields["primary_slot"]["remaining_shots"] == 0


def test_framing_only_payload_is_partial_and_does_not_expose_opaque_bytes() -> None:
    result = decode_characteristic(
        service_uuid="8000ee00-ee00-ffff-ffff-ffffffffffff",
        uuid="0000ee02-0000-1000-8000-00805f9b34fb",
        handle=257,
        value=bytes.fromhex("0a 00 00 00 00 00 00 00 00 00 00"),
    )

    assert result.status is DecodeStatus.PARTIAL
    assert result.fields == {"declared_length": 10, "message_type": 0, "payload_length": 8}
    assert result.to_dict(include_raw=True, show_sensitive=False)["raw_hex"] is None


def test_state_dependent_errors_are_unavailable_without_losing_metadata() -> None:
    result = decode_characteristic(
        service_uuid=CAMERA_CONTROL_SERVICE,
        uuid="0000cc06-0000-1000-8000-00805f9b34fb",
        handle=54,
        value=None,
        error="BleakGATTProtocolError: (157, 'GATT Protocol Error: Application-specific Error 0x9D')",
    )

    assert result.status is DecodeStatus.UNAVAILABLE
    assert result.fields == {}
    assert result.error is not None


def test_non_state_gatt_error_is_error() -> None:
    result = decode_characteristic(
        service_uuid=CAMERA_CONTROL_SERVICE,
        uuid="0000cc0a-0000-1000-8000-00805f9b34fb",
        handle=63,
        value=None,
        error="BleakError: connection closed unexpectedly",
    )

    assert result.status is DecodeStatus.ERROR


def test_snapshot_schema_redacts_corebluetooth_address() -> None:
    firmware = decode_characteristic(
        service_uuid=CAMERA_CONTROL_SERVICE,
        uuid="0000cc0a-0000-1000-8000-00805f9b34fb",
        handle=63,
        value=b"2.01",
    )
    snapshot = CameraInfoSnapshot.create(
        captured_at="2026-07-30T05:00:00.000+00:00",
        address="00000000-1111-2222-3333-444444444444",
        name="ILCE-7CM2",
        local_name="ILCE-7CM2",
        rssi=-51,
        advertisement={"is_camera": True, "protocol_version": 101, "requires_unlock": True},
        characteristics=(firmware,),
    )

    hidden = snapshot.to_dict(include_raw=False, show_sensitive=False)
    shown = snapshot.to_dict(include_raw=False, show_sensitive=True)

    assert hidden["schema_version"] == 1
    assert hidden["device"]["address"] is None
    assert hidden["device"]["address_redacted"] is True
    assert shown["device"]["address"] == "00000000-1111-2222-3333-444444444444"
    assert hidden["summary"]["firmware_version"] == "2.01"


@pytest.mark.parametrize("include_raw", [False, True])
@pytest.mark.parametrize("show_sensitive", [False, True])
@pytest.mark.parametrize("sensitivity", [Sensitivity.PUBLIC, Sensitivity.SECRET])
@pytest.mark.parametrize(
    ("outcome", "value", "error", "status", "expected_fields", "warning", "expected_error", "retained_value"),
    [
        ("read-error", b"abc", "TimeoutError", "unavailable", {}, None, "TimeoutError", None),
        ("read-error", None, "OSError", "error", {}, None, "OSError", None),
        ("read-error", b"abc", "", "error", {}, None, "", None),
        ("missing", None, None, "error", {}, None, "Characteristic returned no value.", None),
        (
            "unknown",
            b"abc",
            None,
            "unknown",
            {},
            "No evidence-backed decoder is registered for this payload.",
            None,
            b"abc",
        ),
        ("decoded", b"abc", None, "decoded", {"sample": "value"}, None, None, b"abc"),
        ("partial", b"abc", None, "partial", {"sample": None}, "Partial fixture.", None, b"abc"),
        ("exception", b"abc", None, "partial", {}, "Malformed payload: ValueError: invalid fixture", None, b"abc"),
    ],
)
def test_decode_outcomes_preserve_complete_serialization(
    monkeypatch,
    include_raw,
    show_sensitive,
    sensitivity,
    outcome,
    value,
    error,
    status,
    expected_fields,
    warning,
    expected_error,
    retained_value,
) -> None:
    uuid = "0000ccfe-0000-1000-8000-00805f9b34fb"
    decoded_values = []

    def decoder(payload):
        decoded_values.append(payload)
        if outcome == "exception":
            raise ValueError("invalid fixture")
        if outcome == "partial":
            return ParseResult(DecodeStatus.PARTIAL, {"sample": None}, "Partial fixture.")
        return ParseResult(DecodeStatus.DECODED, {"sample": "value"})

    monkeypatch.setitem(
        sony_info.CHARACTERISTIC_SPECS,
        uuid,
        CharacteristicSpec(
            name="Fixture",
            category="camera_status",
            confidence=Confidence.TENTATIVE,
            sensitivity=sensitivity,
            decoder=None if outcome == "unknown" else decoder,
        ),
    )
    result = decode_characteristic(
        service_uuid=CAMERA_CONTROL_SERVICE.upper(),
        uuid=uuid.upper(),
        handle=123,
        value=value,
        error=error,
    )

    redacted = sensitivity is Sensitivity.SECRET and not show_sensitive
    raw_allowed = include_raw and (sensitivity is Sensitivity.PUBLIC or show_sensitive)
    fields = dict.fromkeys(expected_fields) if redacted else expected_fields
    assert result.value == retained_value
    assert decoded_values == ([value] if outcome in {"decoded", "partial", "exception"} else [])
    assert result.to_dict(include_raw=include_raw, show_sensitive=show_sensitive) == {
        "service_uuid": CAMERA_CONTROL_SERVICE.upper(),
        "uuid": uuid,
        "handle": 123,
        "name": "Fixture",
        "category": "camera_status",
        "status": status,
        "confidence": "tentative",
        "fields": fields,
        "value_len": len(retained_value) if retained_value is not None else None,
        "raw_hex": "61 62 63" if raw_allowed and retained_value is not None else None,
        "sensitivity": sensitivity.value,
        "redacted": redacted,
        "warning": warning,
        "error": expected_error,
    }


@pytest.mark.parametrize(
    "error",
    [
        IndexError("truncated"),
        UnicodeDecodeError("ascii", b"\xff", 0, 1, "invalid"),
        ValueError("invalid"),
        OverflowError("large"),
    ],
)
def test_decoder_payload_exceptions_remain_partial_with_raw_value(monkeypatch, error) -> None:
    uuid = "0000ccfe-0000-1000-8000-00805f9b34fb"

    def decoder(_payload):
        raise error

    monkeypatch.setitem(
        sony_info.CHARACTERISTIC_SPECS,
        uuid,
        CharacteristicSpec("Fixture", "identity", Confidence.UNKNOWN, Sensitivity.PUBLIC, decoder),
    )
    result = decode_characteristic(service_uuid=CAMERA_CONTROL_SERVICE, uuid=uuid, handle=None, value=b"\xff")

    assert result.status is DecodeStatus.PARTIAL
    assert result.fields == {}
    assert result.value == b"\xff"
    assert result.error is None
    assert result.warning == f"Malformed payload: {type(error).__name__}: {error}"
