from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from sonygeotag.ble_probe import bytes_to_hex

SCHEMA_VERSION = 1


class DecodeStatus(StrEnum):
    DECODED = "decoded"
    PARTIAL = "partial"
    UNKNOWN = "unknown"
    UNAVAILABLE = "unavailable"
    ERROR = "error"


class Confidence(StrEnum):
    VERIFIED = "verified"
    REFERENCED = "referenced"
    TENTATIVE = "tentative"
    UNKNOWN = "unknown"


class Sensitivity(StrEnum):
    PUBLIC = "public"
    NETWORK = "network"
    SECRET = "secret"
    IDENTIFIER = "identifier"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ParseResult:
    status: DecodeStatus
    fields: dict[str, Any]
    warning: str | None = None


Decoder = Callable[[bytes], ParseResult]


@dataclass(frozen=True)
class CharacteristicSpec:
    name: str
    category: str
    confidence: Confidence
    sensitivity: Sensitivity
    decoder: Decoder | None = None


@dataclass(frozen=True)
class DecodedCharacteristic:
    service_uuid: str
    uuid: str
    handle: int | None
    name: str
    category: str
    status: DecodeStatus
    confidence: Confidence
    fields: dict[str, Any]
    value: bytes | None
    sensitivity: Sensitivity
    warning: str | None = None
    error: str | None = None

    @property
    def value_len(self) -> int | None:
        return len(self.value) if self.value is not None else None

    def to_dict(self, *, include_raw: bool, show_sensitive: bool) -> dict[str, Any]:
        is_sensitive = self.sensitivity is not Sensitivity.PUBLIC
        redacted = is_sensitive and not show_sensitive
        fields = dict.fromkeys(self.fields) if redacted else self.fields
        raw_allowed = include_raw and (not is_sensitive or show_sensitive)
        return {
            "service_uuid": self.service_uuid,
            "uuid": self.uuid,
            "handle": self.handle,
            "name": self.name,
            "category": self.category,
            "status": self.status.value,
            "confidence": self.confidence.value,
            "fields": fields,
            "value_len": self.value_len,
            "raw_hex": bytes_to_hex(self.value) if raw_allowed and self.value is not None else None,
            "sensitivity": self.sensitivity.value,
            "redacted": redacted,
            "warning": self.warning,
            "error": self.error,
        }


@dataclass(frozen=True)
class CameraInfoDevice:
    address: str
    name: str | None
    local_name: str | None
    rssi: int | None

    def to_dict(self, *, show_sensitive: bool) -> dict[str, Any]:
        return {
            "address": self.address if show_sensitive else None,
            "address_redacted": not show_sensitive,
            "name": self.name,
            "local_name": self.local_name,
            "rssi": self.rssi,
        }


@dataclass(frozen=True)
class CameraInfoSnapshot:
    captured_at: str
    device: CameraInfoDevice
    advertisement: dict[str, bool | int | None] | None
    characteristics: tuple[DecodedCharacteristic, ...]

    @property
    def summary(self) -> dict[str, Any]:
        return snapshot_summary(self.characteristics)

    @classmethod
    def create(
        cls,
        *,
        captured_at: str,
        address: str,
        name: str | None,
        local_name: str | None,
        rssi: int | None,
        advertisement: dict[str, bool | int | None] | None,
        characteristics: tuple[DecodedCharacteristic, ...],
    ) -> CameraInfoSnapshot:
        return cls(
            captured_at=captured_at,
            device=CameraInfoDevice(
                address=address,
                name=name,
                local_name=local_name,
                rssi=rssi,
            ),
            advertisement=advertisement,
            characteristics=characteristics,
        )

    def to_dict(self, *, include_raw: bool, show_sensitive: bool) -> dict[str, Any]:
        counts = {status.value: 0 for status in DecodeStatus}
        for characteristic in self.characteristics:
            counts[characteristic.status.value] += 1
        return {
            "schema_version": SCHEMA_VERSION,
            "captured_at": self.captured_at,
            "device": self.device.to_dict(show_sensitive=show_sensitive),
            "advertisement": self.advertisement,
            "summary": self.summary,
            "counts": counts,
            "characteristics": [
                characteristic.to_dict(include_raw=include_raw, show_sensitive=show_sensitive)
                for characteristic in self.characteristics
            ],
        }


def _uuid(short: str) -> str:
    return f"0000{short.lower()}-0000-1000-8000-00805f9b34fb"


def _spec(
    name: str,
    category: str,
    confidence: Confidence,
    sensitivity: Sensitivity = Sensitivity.PUBLIC,
    decoder: Decoder | None = None,
) -> CharacteristicSpec:
    return CharacteristicSpec(
        name=name,
        category=category,
        confidence=confidence,
        sensitivity=sensitivity,
        decoder=decoder,
    )


def _join_warnings(warnings: list[str]) -> str | None:
    return "; ".join(warnings) if warnings else None


def _ascii(value: bytes, field: str) -> ParseResult:
    try:
        decoded = value.rstrip(b"\x00").decode("ascii")
    except UnicodeDecodeError:
        return ParseResult(
            status=DecodeStatus.PARTIAL,
            fields={field: None},
            warning="Payload is not valid ASCII.",
        )
    if not decoded:
        return ParseResult(
            status=DecodeStatus.PARTIAL,
            fields={field: None},
            warning="ASCII payload is empty.",
        )
    if any(not 0x20 <= ord(character) <= 0x7E for character in decoded):
        return ParseResult(
            status=DecodeStatus.PARTIAL,
            fields={field: None},
            warning="ASCII payload contains control characters.",
        )
    return ParseResult(status=DecodeStatus.DECODED, fields={field: decoded})


def _framed_payload(value: bytes) -> tuple[bytes | None, list[str]]:
    if len(value) < 3:
        return None, ["Sony frame is shorter than its 3-byte header."]
    warnings: list[str] = []
    if value[0] != len(value) - 1:
        warnings.append(f"Declared length {value[0]} does not match payload length {len(value) - 1}.")
    return value[3:], warnings


def _optional_framed_ascii(value: bytes, field: str) -> ParseResult:
    payload = value
    warnings: list[str] = []
    if len(value) >= 3 and value[0] == len(value) - 1:
        payload = value[3:]
    result = _ascii(payload, field)
    if result.warning:
        warnings.append(result.warning)
    return ParseResult(
        status=DecodeStatus.PARTIAL if warnings else result.status,
        fields=result.fields,
        warning=_join_warnings(warnings),
    )


def _framed_ascii(value: bytes, field: str) -> ParseResult:
    payload, warnings = _framed_payload(value)
    if payload is None:
        return ParseResult(status=DecodeStatus.PARTIAL, fields={field: None}, warning=_join_warnings(warnings))
    result = _ascii(payload, field)
    if result.warning:
        warnings.append(result.warning)
    return ParseResult(
        status=DecodeStatus.PARTIAL if warnings else result.status,
        fields=result.fields,
        warning=_join_warnings(warnings),
    )


def _decode_firmware(value: bytes) -> ParseResult:
    return _ascii(value, "firmware_version")


def _decode_model(value: bytes) -> ParseResult:
    return _ascii(value, "model")


def _decode_framed_model(value: bytes) -> ParseResult:
    return _framed_ascii(value, "model")


def _decode_ssid(value: bytes) -> ParseResult:
    return _optional_framed_ascii(value, "ssid")


def _decode_wifi_password(value: bytes) -> ParseResult:
    return _optional_framed_ascii(value, "password")


def _decode_bssid(value: bytes) -> ParseResult:
    return _optional_framed_ascii(value, "bssid")


def _decode_ftp_profiles(value: bytes) -> ParseResult:
    payload, warnings = _framed_payload(value)
    if payload is None:
        return ParseResult(
            status=DecodeStatus.PARTIAL,
            fields={"ftp_profile_names": None},
            warning=_join_warnings(warnings),
        )
    try:
        text = payload.rstrip(b"\x00").decode("ascii")
    except UnicodeDecodeError:
        warnings.append("FTP profile payload is not valid ASCII.")
        return ParseResult(
            status=DecodeStatus.PARTIAL,
            fields={"ftp_profile_names": None},
            warning=_join_warnings(warnings),
        )
    profiles = [line for line in text.splitlines() if line]
    return ParseResult(
        status=DecodeStatus.PARTIAL if warnings else DecodeStatus.DECODED,
        fields={"ftp_profile_names": profiles},
        warning=_join_warnings(warnings),
    )


def _decode_push_transfer(value: bytes) -> ParseResult:
    payload, warnings = _framed_payload(value)
    if not payload:
        warnings.append("Push-transfer frame has no state byte.")
        return ParseResult(status=DecodeStatus.PARTIAL, fields={}, warning=_join_warnings(warnings))
    code = payload[0]
    state = {1: "idle", 2: "ready"}.get(code, "unknown")
    if state == "unknown":
        warnings.append(f"Unknown push-transfer state code {code}.")
    return ParseResult(
        status=DecodeStatus.PARTIAL if warnings else DecodeStatus.DECODED,
        fields={"push_transfer_code": code, "push_transfer_state": state},
        warning=_join_warnings(warnings),
    )


def _parse_length_tagged_records(value: bytes) -> tuple[list[tuple[int, bytes]], list[str]]:
    records: list[tuple[int, bytes]] = []
    warnings: list[str] = []
    offset = 0
    while offset < len(value):
        record_length = value[offset]
        if record_length < 2:
            warnings.append(f"Invalid TLV record length {record_length} at offset {offset}.")
            break
        end = offset + 1 + record_length
        if end > len(value):
            warnings.append(
                f"Truncated TLV record at offset {offset}: needs {record_length} byte(s), "
                f"has {len(value) - offset - 1}."
            )
            break
        tag = int.from_bytes(value[offset + 1 : offset + 3], byteorder="big")
        records.append((tag, value[offset + 3 : end]))
        offset = end
    return records, warnings


def _decode_bool(value: int) -> bool | int:
    return bool(value) if value in (0, 1) else value


def _decode_camera_status(value: bytes) -> ParseResult:
    records, warnings = _parse_length_tagged_records(value)
    fields: dict[str, Any] = {}
    unknown_tags: dict[str, list[int]] = {}
    duplicate_tags: list[str] = []
    seen: set[int] = set()
    known_names = {
        0x0002: "image_transfer_available",
        0x0003: "remote_control_available",
        0x0005: "time_setting_complete",
        0x0007: "live_streaming",
        0x0008: "movie_recording",
        0x0009: "streaming_mode",
        0x000A: "background_transfer_available",
    }
    for tag, payload in records:
        tag_label = f"0x{tag:04x}"
        if tag in seen:
            duplicate_tags.append(tag_label)
            continue
        seen.add(tag)
        numeric_value = int.from_bytes(payload, byteorder="big") if payload else 0
        if tag == 0x0001:
            fields["wifi_state_code"] = numeric_value
            fields["wifi_state"] = {
                0: "terminated",
                1: "launching",
                2: "launched",
                3: "terminating",
            }.get(numeric_value, "unknown")
            if fields["wifi_state"] == "unknown":
                warnings.append(f"Unknown Wi-Fi state code {numeric_value}.")
        elif tag in known_names:
            fields[known_names[tag]] = _decode_bool(numeric_value)
        else:
            unknown_tags[tag_label] = [numeric_value]
    if unknown_tags:
        fields["unknown_tags"] = unknown_tags
    if duplicate_tags:
        fields["duplicate_tags"] = duplicate_tags
        warnings.append(f"Duplicate TLV tag(s): {', '.join(duplicate_tags)}.")
    if unknown_tags:
        warnings.append(f"Unknown TLV tag(s) retained: {', '.join(unknown_tags)}.")
    return ParseResult(
        status=DecodeStatus.PARTIAL if warnings else DecodeStatus.DECODED,
        fields=fields,
        warning=_join_warnings(warnings),
    )


def _power_state(power_code: int) -> str:
    return {
        0: "indefinite",
        1: "not_powered",
        2: "unknown",
        3: "usb_power",
    }.get(power_code, "unknown")


def _decode_battery(value: bytes) -> ParseResult:
    warnings: list[str] = []
    header_size = 6
    pack_size = 8
    trailer_size = 5
    if len(value) < header_size + trailer_size:
        return ParseResult(
            status=DecodeStatus.PARTIAL,
            fields={},
            warning="Truncated battery frame: missing pack or power-supply fields.",
        )
    if value[0] != len(value) - 1:
        warnings.append(f"Declared length {value[0]} does not match payload length {len(value) - 1}.")
    if value[-5:-1] != bytes(4):
        warnings.append("Battery power-supply trailer contains non-zero reserved bytes.")

    pack_payload = value[header_size:-trailer_size]
    complete_pack_bytes = len(pack_payload) - (len(pack_payload) % pack_size)
    if complete_pack_bytes != len(pack_payload):
        warnings.append(f"Truncated battery pack: {len(pack_payload) - complete_pack_bytes} trailing byte(s).")
    batteries: list[dict[str, int]] = []
    for offset in range(0, complete_pack_bytes, pack_size):
        pack = pack_payload[offset : offset + pack_size]
        remaining_percent = int.from_bytes(pack[4:8], byteorder="big")
        batteries.append(
            {
                "slot_flags": pack[0],
                "position_code": pack[1],
                "level_code": pack[2],
                "reserved_code": pack[3],
                "remaining_percent": remaining_percent,
            }
        )
        if remaining_percent > 100:
            warnings.append(f"Battery percentage {remaining_percent} is outside 0..100.")
    if not batteries:
        warnings.append("Battery frame contains no complete battery pack.")

    power_code = value[-1]
    fields: dict[str, Any] = {
        "protocol_version": value[3],
        "feature_flags": value[4],
        "battery_group_code": value[5],
        "batteries": batteries,
        "external_power_code": power_code,
        "external_power_state": _power_state(power_code),
    }
    return ParseResult(
        status=DecodeStatus.PARTIAL if warnings else DecodeStatus.DECODED,
        fields=fields,
        warning=_join_warnings(warnings),
    )


def _decode_media(value: bytes) -> ParseResult:
    warnings: list[str] = []
    if len(value) < 16:
        return ParseResult(
            status=DecodeStatus.PARTIAL,
            fields={},
            warning="Media frame is shorter than the verified primary-slot prefix.",
        )
    if value[0] != len(value) - 1:
        warnings.append(f"Declared length {value[0]} does not match payload length {len(value) - 1}.")
    status_code = value[6]
    primary_slot = {
        "position_code": value[5],
        "status_code": status_code,
        "status": {0: "absent", 1: "present", 2: "format_required"}.get(status_code, "unknown"),
        "reserved_code": value[7],
        "remaining_shots": int.from_bytes(value[8:12], byteorder="big"),
        "remaining_recording_seconds": int.from_bytes(value[12:16], byteorder="big"),
    }
    trailing_bytes = len(value) - 16
    fields: dict[str, Any] = {
        "protocol_version": value[3],
        "media_flags": value[4],
        "primary_slot": primary_slot,
        "unparsed_trailing_bytes": trailing_bytes,
    }
    warnings.append(
        "Only the primary media-slot prefix is tentatively decoded; trailing vendor bytes remain uninterpreted."
    )
    return ParseResult(status=DecodeStatus.PARTIAL, fields=fields, warning=_join_warnings(warnings))


def _decode_wifi_band(value: bytes) -> ParseResult:
    payload, warnings = _framed_payload(value)
    if payload is None or len(payload) < 2:
        warnings.append("Wi-Fi band frame does not contain both message and band codes.")
        return ParseResult(status=DecodeStatus.PARTIAL, fields={}, warning=_join_warnings(warnings))
    fields = {"message_code": payload[0], "wifi_band_code": payload[1]}
    warnings.append("Wi-Fi band code is retained numerically because its A7C II mapping is not verified.")
    return ParseResult(status=DecodeStatus.PARTIAL, fields=fields, warning=_join_warnings(warnings))


def _decode_location_feature(value: bytes) -> ParseResult:
    if len(value) < 5:
        return ParseResult(
            status=DecodeStatus.PARTIAL,
            fields={},
            warning="Location feature payload is shorter than 5 bytes.",
        )
    timezone_supported = (value[4] & 0x02) == 0x02
    return ParseResult(
        status=DecodeStatus.DECODED,
        fields={
            "timezone_supported": timezone_supported,
            "location_packet_size": 95 if timezone_supported else 91,
            "feature_flags": value[4],
        },
    )


def _single_bool(value: bytes, field: str) -> ParseResult:
    if len(value) != 1:
        return ParseResult(
            status=DecodeStatus.PARTIAL,
            fields={field: None},
            warning=f"Expected exactly 1 byte, received {len(value)}.",
        )
    if value[0] not in (0, 1):
        return ParseResult(
            status=DecodeStatus.PARTIAL,
            fields={field: None, f"{field}_code": value[0]},
            warning=f"Expected boolean code 0 or 1, received {value[0]}.",
        )
    return ParseResult(status=DecodeStatus.DECODED, fields={field: value[0] == 1})


def _decode_location_lock(value: bytes) -> ParseResult:
    return _single_bool(value, "location_locked")


def _decode_location_enabled(value: bytes) -> ParseResult:
    return _single_bool(value, "location_transfer_enabled")


def _decode_time_correction(value: bytes) -> ParseResult:
    return _single_bool(value, "time_correction_enabled")


def _decode_area_adjustment(value: bytes) -> ParseResult:
    return _single_bool(value, "area_adjustment_enabled")


def _decode_framing_only(value: bytes) -> ParseResult:
    if len(value) < 3:
        return ParseResult(
            status=DecodeStatus.UNKNOWN,
            fields={},
            warning="Payload is too short to validate Sony framing.",
        )
    fields = {
        "declared_length": value[0],
        "message_type": int.from_bytes(value[1:3], byteorder="big"),
        "payload_length": len(value) - 3,
    }
    warning = "Framing is recognized, but payload semantics are not verified."
    if value[0] != len(value) - 1:
        warning = f"Declared length {value[0]} does not match payload length {len(value) - 1}; {warning}"
    return ParseResult(status=DecodeStatus.PARTIAL, fields=fields, warning=warning)


CHARACTERISTIC_SPECS: dict[str, CharacteristicSpec] = {
    _uuid("cc03"): _spec("Push transfer", "camera_status", Confidence.REFERENCED, decoder=_decode_push_transfer),
    _uuid("cc06"): _spec("Wi-Fi SSID", "network", Confidence.REFERENCED, Sensitivity.NETWORK, _decode_ssid),
    _uuid("cc07"): _spec(
        "Wi-Fi password", "network", Confidence.REFERENCED, Sensitivity.SECRET, _decode_wifi_password
    ),
    _uuid("cc09"): _spec("Camera status", "camera_status", Confidence.REFERENCED, decoder=_decode_camera_status),
    _uuid("cc0a"): _spec("Firmware version", "identity", Confidence.VERIFIED, decoder=_decode_firmware),
    _uuid("cc0b"): _spec("Camera model", "identity", Confidence.VERIFIED, decoder=_decode_model),
    _uuid("cc0c"): _spec("Wi-Fi BSSID", "network", Confidence.REFERENCED, Sensitivity.NETWORK, _decode_bssid),
    _uuid("cc0d"): _spec("Device information", "identity", Confidence.UNKNOWN, decoder=_decode_framing_only),
    _uuid("cc0f"): _spec("Media status", "storage", Confidence.TENTATIVE, decoder=_decode_media),
    _uuid("cc10"): _spec("Battery status", "battery", Confidence.REFERENCED, decoder=_decode_battery),
    _uuid("cc11"): _spec("Framed camera model", "identity", Confidence.VERIFIED, decoder=_decode_framed_model),
    _uuid("cc12"): _spec("Date format", "camera_status", Confidence.UNKNOWN, decoder=_decode_framing_only),
    _uuid("cc15"): _spec("Opaque device identifier", "identity", Confidence.UNKNOWN, Sensitivity.IDENTIFIER),
    _uuid("cc16"): _spec("Opaque device data", "identity", Confidence.UNKNOWN, Sensitivity.IDENTIFIER),
    _uuid("cc17"): _spec("Opaque device token", "identity", Confidence.UNKNOWN, Sensitivity.IDENTIFIER),
    _uuid("cc40"): _spec(
        "FTP profile names", "network", Confidence.REFERENCED, Sensitivity.NETWORK, _decode_ftp_profiles
    ),
    _uuid("cca2"): _spec("Opaque network identifier", "network", Confidence.UNKNOWN, Sensitivity.IDENTIFIER),
    _uuid("cca7"): _spec("Opaque network token", "network", Confidence.UNKNOWN, Sensitivity.IDENTIFIER),
    _uuid("ccab"): _spec("Wi-Fi band", "network", Confidence.REFERENCED, decoder=_decode_wifi_band),
    _uuid("dd21"): _spec(
        "Location capabilities", "location", Confidence.VERIFIED, decoder=_decode_location_feature
    ),
    _uuid("dd30"): _spec("Location lock", "location", Confidence.VERIFIED, decoder=_decode_location_lock),
    _uuid("dd31"): _spec(
        "Location transfer", "location", Confidence.VERIFIED, decoder=_decode_location_enabled
    ),
    _uuid("dd32"): _spec("Time correction", "location", Confidence.VERIFIED, decoder=_decode_time_correction),
    _uuid("dd33"): _spec("Area adjustment", "location", Confidence.VERIFIED, decoder=_decode_area_adjustment),
    _uuid("ee02"): _spec(
        "Pairing information", "pairing", Confidence.UNKNOWN, Sensitivity.IDENTIFIER, _decode_framing_only
    ),
    _uuid("ee04"): _spec(
        "Pairing device information", "pairing", Confidence.UNKNOWN, Sensitivity.IDENTIFIER, _decode_framing_only
    ),
}


SENSITIVE_UNKNOWN_UUIDS = {
    _uuid("cca1"),
    _uuid("cca7"),
    _uuid("ccad"),
    _uuid("ccaf"),
    _uuid("ccb0"),
}


def _default_category(service_uuid: str) -> str:
    service = service_uuid.lower()
    if service.startswith("8000cc00"):
        return "camera_control"
    if service.startswith("8000dd00"):
        return "location"
    if service.startswith("8000ee00"):
        return "pairing"
    if service.startswith("8000bb00"):
        return "protocol"
    if service.startswith("8000ff00"):
        return "remote"
    return "unknown"


def _default_spec(service_uuid: str, uuid: str) -> CharacteristicSpec:
    sensitivity = Sensitivity.IDENTIFIER if uuid.lower() in SENSITIVE_UNKNOWN_UUIDS else Sensitivity.UNKNOWN
    return CharacteristicSpec(
        name="Unknown Sony characteristic",
        category=_default_category(service_uuid),
        confidence=Confidence.UNKNOWN,
        sensitivity=sensitivity,
        decoder=None,
    )


def _error_status(error: str) -> DecodeStatus:
    normalized = error.lower()
    unavailable_markers = (
        "0x90",
        "0x9d",
        "insufficient encryption",
        "insufficient authentication",
        "timeout",
        "timed out",
    )
    if any(marker in normalized for marker in unavailable_markers):
        return DecodeStatus.UNAVAILABLE
    return DecodeStatus.ERROR


def decode_characteristic(
    *,
    service_uuid: str,
    uuid: str,
    handle: int | None,
    value: bytes | None,
    error: str | None = None,
) -> DecodedCharacteristic:
    normalized_uuid = uuid.lower()
    spec = CHARACTERISTIC_SPECS.get(normalized_uuid, _default_spec(service_uuid, normalized_uuid))
    if error is not None:
        return DecodedCharacteristic(
            service_uuid=service_uuid,
            uuid=normalized_uuid,
            handle=handle,
            name=spec.name,
            category=spec.category,
            status=_error_status(error),
            confidence=spec.confidence,
            fields={},
            value=None,
            sensitivity=spec.sensitivity,
            error=error,
        )
    if value is None:
        return DecodedCharacteristic(
            service_uuid=service_uuid,
            uuid=normalized_uuid,
            handle=handle,
            name=spec.name,
            category=spec.category,
            status=DecodeStatus.ERROR,
            confidence=spec.confidence,
            fields={},
            value=None,
            sensitivity=spec.sensitivity,
            error="Characteristic returned no value.",
        )
    if spec.decoder is None:
        return DecodedCharacteristic(
            service_uuid=service_uuid,
            uuid=normalized_uuid,
            handle=handle,
            name=spec.name,
            category=spec.category,
            status=DecodeStatus.UNKNOWN,
            confidence=spec.confidence,
            fields={},
            value=value,
            sensitivity=spec.sensitivity,
            warning="No evidence-backed decoder is registered for this payload.",
        )
    try:
        parsed = spec.decoder(value)
    except (IndexError, UnicodeDecodeError, ValueError, OverflowError) as error_value:
        parsed = ParseResult(
            status=DecodeStatus.PARTIAL,
            fields={},
            warning=f"Malformed payload: {type(error_value).__name__}: {error_value}",
        )
    return DecodedCharacteristic(
        service_uuid=service_uuid,
        uuid=normalized_uuid,
        handle=handle,
        name=spec.name,
        category=spec.category,
        status=parsed.status,
        confidence=spec.confidence,
        fields=parsed.fields,
        value=value,
        sensitivity=spec.sensitivity,
        warning=parsed.warning,
    )


def _first_field(
    characteristics: tuple[DecodedCharacteristic, ...],
    field: str,
) -> object | None:
    for characteristic in characteristics:
        value = characteristic.fields.get(field)
        if value is not None:
            return value
    return None


def snapshot_summary(characteristics: tuple[DecodedCharacteristic, ...]) -> dict[str, Any]:
    batteries = _first_field(characteristics, "batteries")
    primary_battery = batteries[0] if isinstance(batteries, list) and batteries else None
    primary_slot = _first_field(characteristics, "primary_slot")
    camera_status_fields = {
        key: _first_field(characteristics, key)
        for key in (
            "wifi_state",
            "image_transfer_available",
            "remote_control_available",
            "time_setting_complete",
            "live_streaming",
            "movie_recording",
            "streaming_mode",
            "background_transfer_available",
        )
    }
    camera_status = {key: value for key, value in camera_status_fields.items() if value is not None}
    location_fields = {
        key: _first_field(characteristics, key)
        for key in (
            "timezone_supported",
            "location_packet_size",
            "location_locked",
            "location_transfer_enabled",
            "time_correction_enabled",
            "area_adjustment_enabled",
        )
    }
    location = {key: value for key, value in location_fields.items() if value is not None}
    return {
        "model": _first_field(characteristics, "model"),
        "firmware_version": _first_field(characteristics, "firmware_version"),
        "battery_percent": primary_battery.get("remaining_percent") if isinstance(primary_battery, dict) else None,
        "external_power_state": _first_field(characteristics, "external_power_state"),
        "primary_media": primary_slot,
        "camera_status": camera_status,
        "location": location,
    }
