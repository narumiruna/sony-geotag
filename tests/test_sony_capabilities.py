from __future__ import annotations

import pytest

from sonygeotag import sony_capabilities
from sonygeotag.sony_capabilities import SonyCompatibilityEntry
from sonygeotag.sony_capabilities import SonyGattDescriptor
from sonygeotag.sony_capabilities import SonyIdentity
from sonygeotag.sony_capabilities import SonyLocationProfileKind
from sonygeotag.sony_capabilities import SonySupportConfidence
from sonygeotag.sony_capabilities import compatibility_for
from sonygeotag.sony_capabilities import experimental_approval_key
from sonygeotag.sony_capabilities import parse_dd21_mode
from sonygeotag.sony_capabilities import resolve_compatible_profile
from sonygeotag.sony_capabilities import resolve_location_profile
from sonygeotag.sony_protocol import LOCATION_CONFIG_READ_UUID
from sonygeotag.sony_protocol import LOCATION_DATA_WRITE_UUID
from sonygeotag.sony_protocol import LOCATION_ENABLE_UUID
from sonygeotag.sony_protocol import LOCATION_LOCK_UUID
from sonygeotag.sony_protocol import LOCATION_SERVICE_UUID
from sonygeotag.sony_protocol import LOCATION_STATUS_NOTIFY_UUID


def descriptor(uuid: str, *properties: str, service: str = LOCATION_SERVICE_UUID) -> SonyGattDescriptor:
    return SonyGattDescriptor.create(
        service_uuid=service,
        characteristic_uuid=uuid,
        properties=properties,
    )


def legacy_shape() -> list[SonyGattDescriptor]:
    return [
        descriptor(LOCATION_DATA_WRITE_UUID, "write"),
        descriptor(LOCATION_CONFIG_READ_UUID, "read"),
    ]


def modern_shape() -> list[SonyGattDescriptor]:
    return [
        *legacy_shape(),
        descriptor(LOCATION_LOCK_UUID, "read", "write"),
        descriptor(LOCATION_ENABLE_UUID, "read", "write"),
        descriptor(LOCATION_STATUS_NOTIFY_UUID, "notify"),
    ]


@pytest.mark.parametrize("version", [65, 101, 255])
def test_modern_profile_requires_complete_shape_and_protocol_threshold(version: int) -> None:
    profile = resolve_location_profile(
        protocol_version=version,
        descriptors=modern_shape(),
        discovery_complete=True,
    )

    assert profile.kind is SonyLocationProfileKind.MODERN
    assert profile.has_status_notifications is True
    assert profile.experimental is False


def test_known_old_protocol_without_controls_selects_legacy() -> None:
    profile = resolve_location_profile(protocol_version=64, descriptors=legacy_shape(), discovery_complete=True)

    assert profile.kind is SonyLocationProfileKind.LEGACY
    assert profile.executable is True


def test_unknown_version_complete_modern_shape_is_experimental_modern() -> None:
    profile = resolve_location_profile(protocol_version=None, descriptors=modern_shape(), discovery_complete=True)

    assert profile.kind is SonyLocationProfileKind.MODERN
    assert profile.experimental is True


@pytest.mark.parametrize(
    ("version", "descriptors", "reason"),
    [
        (64, modern_shape(), "unexpectedly exposes"),
        (65, legacy_shape(), "requires writable"),
        (None, legacy_shape(), "Unknown-version"),
        (
            101,
            [*modern_shape()[:3], descriptor(LOCATION_ENABLE_UUID, "write-without-response"), *modern_shape()[4:]],
            "DD30/DD31",
        ),
        (101, [*legacy_shape(), descriptor(LOCATION_LOCK_UUID, "write")], "Only one"),
        (101, legacy_shape()[1:], "DD11 is missing"),
        (101, legacy_shape()[:1], "DD21 is missing"),
        (101, [*modern_shape(), descriptor(LOCATION_DATA_WRITE_UUID, "write")], "Duplicate characteristic"),
        (
            101,
            [
                descriptor(LOCATION_DATA_WRITE_UUID, "write", service="8000ff00-ff00-ffff-ffff-ffffffffffff"),
                descriptor(LOCATION_CONFIG_READ_UUID, "read"),
            ],
            "DD11 is missing",
        ),
    ],
)
def test_inconsistent_shapes_fail_closed(
    version: int | None,
    descriptors: list[SonyGattDescriptor],
    reason: str,
) -> None:
    profile = resolve_location_profile(
        protocol_version=version,
        descriptors=descriptors,
        discovery_complete=True,
    )

    assert profile.kind is SonyLocationProfileKind.UNSUPPORTED
    assert reason in profile.reason


def test_approval_key_normalizes_model_alias_and_separates_purpose() -> None:
    profile = resolve_location_profile(
        protocol_version=101,
        descriptors=modern_shape(),
        discovery_complete=True,
    )
    canonical = SonyIdentity("ILCE-7M4", "4.00", 101)
    alias = SonyIdentity("LE_ILCE-7M4", "4.00", 101)

    location_key = experimental_approval_key(canonical, profile, purpose="location-sync")

    assert location_key == experimental_approval_key(alias, profile, purpose="location-sync")
    assert location_key != experimental_approval_key(canonical, profile, purpose="pair-init")


def test_registry_entries_require_exact_readable_firmware_and_protocol(monkeypatch) -> None:
    monkeypatch.setattr(
        sony_capabilities,
        "VERIFIED_COMPATIBILITY",
        (
            SonyCompatibilityEntry(
                model="ILCE-7M4",
                firmware=None,
                protocol_version=None,
                profile=SonyLocationProfileKind.MODERN,
                confidence=SonySupportConfidence.VERIFIED,
                evidence="overbroad-fixture",
            ),
        ),
    )
    profile = resolve_location_profile(
        protocol_version=101,
        descriptors=modern_shape(),
        discovery_complete=True,
    )

    assert (
        compatibility_for(SonyIdentity("ILCE-7M4", "4.00", 101), profile).confidence
        is SonySupportConfidence.EXPERIMENTAL
    )
    assert (
        compatibility_for(SonyIdentity("ILCE-7M4", None, None), profile).confidence
        is SonySupportConfidence.EXPERIMENTAL
    )


def test_registry_block_converts_executable_shape_to_unsupported(monkeypatch) -> None:
    monkeypatch.setattr(
        sony_capabilities,
        "UNSUPPORTED_COMPATIBILITY",
        (
            SonyCompatibilityEntry(
                model="ILCE-7M4",
                firmware="4.00",
                protocol_version=101,
                profile=SonyLocationProfileKind.MODERN,
                confidence=SonySupportConfidence.UNSUPPORTED,
                evidence="blocked-fixture",
            ),
        ),
    )
    profile, compatibility = resolve_compatible_profile(
        identity=SonyIdentity("ILCE-7M4", "4.00", 101),
        protocol_version=101,
        descriptors=modern_shape(),
    )

    assert profile.kind is SonyLocationProfileKind.UNSUPPORTED
    assert compatibility.confidence is SonySupportConfidence.UNSUPPORTED


def test_incomplete_discovery_and_registry_block_fail_closed() -> None:
    incomplete = resolve_location_profile(
        protocol_version=101,
        descriptors=modern_shape(),
        discovery_complete=False,
    )
    blocked = resolve_location_profile(
        protocol_version=101,
        descriptors=modern_shape(),
        discovery_complete=True,
        registry_confidence=SonySupportConfidence.UNSUPPORTED,
    )

    assert incomplete.kind is SonyLocationProfileKind.UNSUPPORTED
    assert blocked.kind is SonyLocationProfileKind.UNSUPPORTED
    assert (
        compatibility_for(SonyIdentity("UNKNOWN", None, 101), incomplete).confidence
        is SonySupportConfidence.UNSUPPORTED
    )


@pytest.mark.parametrize(
    ("payload", "packet_size"),
    [
        (bytes.fromhex("06 10 00 9c 02 00"), 95),
        (bytes.fromhex("06 10 00 9c 02 00 00"), 95),
        (bytes.fromhex("06 10 00 9c 00 00"), 91),
        (bytes.fromhex("06 10 00 9c 00 00 00"), 91),
    ],
)
def test_dd21_accepts_only_evidence_backed_six_or_seven_byte_frames(payload: bytes, packet_size: int) -> None:
    assert parse_dd21_mode(payload).packet_size == packet_size


@pytest.mark.parametrize(
    "payload",
    [
        b"",
        bytes.fromhex("06 10 00 9c 02"),
        bytes.fromhex("06 10 00 9c 02 00 00 00"),
        bytes.fromhex("05 10 00 9c 02 00"),
        bytes.fromhex("06 11 00 9c 02 00"),
        bytes.fromhex("06 10 00 9c 01 00"),
        bytes.fromhex("06 10 00 9c 04 00"),
        bytes.fromhex("06 10 00 9c 02 01"),
        bytes.fromhex("06 10 00 9c 02 00 01"),
    ],
)
def test_dd21_rejects_wrong_length_prefix_flag_and_reserved_bytes(payload: bytes) -> None:
    with pytest.raises(ValueError):
        parse_dd21_mode(payload)
