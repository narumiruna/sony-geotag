from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any
from typing import Protocol

from sonygeotag.sony_protocol import AREA_ADJUSTMENT_UUID
from sonygeotag.sony_protocol import CAMERA_CONTROL_SERVICE_UUID
from sonygeotag.sony_protocol import LOCATION_CONFIG_READ_UUID
from sonygeotag.sony_protocol import LOCATION_DATA_WRITE_UUID
from sonygeotag.sony_protocol import LOCATION_ENABLE_UUID
from sonygeotag.sony_protocol import LOCATION_LOCK_UUID
from sonygeotag.sony_protocol import LOCATION_SERVICE_UUID
from sonygeotag.sony_protocol import LOCATION_STATUS_NOTIFY_UUID
from sonygeotag.sony_protocol import TIME_CORRECTION_UUID

FIRMWARE_VERSION_UUID = "0000cc0a-0000-1000-8000-00805f9b34fb"
CAMERA_MODEL_UUID = "0000cc0b-0000-1000-8000-00805f9b34fb"


class GattCharacteristicLike(Protocol):
    uuid: object
    properties: Iterable[str]


class GattServiceLike(Protocol):
    uuid: object
    characteristics: Iterable[GattCharacteristicLike]


class SonyLocationProfileKind(StrEnum):
    MODERN = "modern"
    LEGACY = "legacy"
    UNSUPPORTED = "unsupported"


class SonySupportConfidence(StrEnum):
    VERIFIED = "verified"
    EXPERIMENTAL = "experimental"
    UNSUPPORTED = "unsupported"


@dataclass(frozen=True)
class SonyGattDescriptor:
    service_uuid: str
    characteristic_uuid: str
    properties: frozenset[str]

    @classmethod
    def create(
        cls,
        *,
        service_uuid: str,
        characteristic_uuid: str,
        properties: set[str] | frozenset[str] | tuple[str, ...] | list[str],
    ) -> SonyGattDescriptor:
        return cls(
            service_uuid=_normalize_uuid(service_uuid),
            characteristic_uuid=_normalize_uuid(characteristic_uuid),
            properties=frozenset(property_name.lower().replace("_", "-") for property_name in properties),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "service_uuid": self.service_uuid,
            "characteristic_uuid": self.characteristic_uuid,
            "properties": sorted(self.properties),
        }


@dataclass(frozen=True)
class SonyLocationProfile:
    kind: SonyLocationProfileKind
    reason: str
    protocol_version: int | None
    experimental: bool
    has_status_notifications: bool = False
    has_time_correction: bool = False
    has_area_adjustment: bool = False

    @property
    def executable(self) -> bool:
        return self.kind is not SonyLocationProfileKind.UNSUPPORTED

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "reason": self.reason,
            "protocol_version": self.protocol_version,
            "experimental": self.experimental,
            "has_status_notifications": self.has_status_notifications,
            "has_time_correction": self.has_time_correction,
            "has_area_adjustment": self.has_area_adjustment,
        }


@dataclass(frozen=True)
class SonyCompatibilityEntry:
    model: str
    firmware: str | None
    protocol_version: int | None
    profile: SonyLocationProfileKind
    confidence: SonySupportConfidence
    evidence: str | None


@dataclass(frozen=True)
class SonyIdentity:
    model: str
    firmware: str | None
    protocol_version: int | None

    @property
    def normalized_model(self) -> str:
        return normalize_model(self.model)

    def to_dict(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "normalized_model": self.normalized_model,
            "firmware": self.firmware,
            "protocol_version": self.protocol_version,
        }


@dataclass(frozen=True)
class SonyDD21Mode:
    include_timezone: bool
    packet_size: int
    value_hex: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "include_timezone": self.include_timezone,
            "packet_size": self.packet_size,
            "value_hex": self.value_hex,
        }


VERIFIED_COMPATIBILITY: tuple[SonyCompatibilityEntry, ...] = ()

UNSUPPORTED_COMPATIBILITY: tuple[SonyCompatibilityEntry, ...] = ()

CANDIDATE_MODELS = (
    "ILCE-7M3",
    "ILCE-7M4",
    "ILCE-6700",
    "ILCE-7RM5",
    "ILCE-7SM3",
    "ILCE-1",
    "ZV-E1",
    "ZV-E10M2",
)


def _normalize_uuid(uuid: str) -> str:
    normalized = uuid.lower()
    if len(normalized) == 4:
        return f"0000{normalized}-0000-1000-8000-00805f9b34fb"
    return normalized


def normalize_model(model: str | None) -> str:
    if not model:
        return "UNKNOWN"
    normalized = model.strip().upper().replace("_", "-").replace(" ", "")
    return normalized.removeprefix("LE-")


def experimental_approval_key(
    identity: SonyIdentity,
    profile: SonyLocationProfile,
    *,
    purpose: str,
) -> str:
    scope = json.dumps(
        {
            "identity": {
                "normalized_model": identity.normalized_model,
                "firmware": identity.firmware,
                "protocol_version": identity.protocol_version,
            },
            "profile": profile.to_dict(),
            "purpose": purpose,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(scope.encode()).hexdigest()[:16]


def resolve_compatible_profile(
    *,
    identity: SonyIdentity,
    protocol_version: int | None,
    descriptors: Iterable[SonyGattDescriptor],
) -> tuple[SonyLocationProfile, SonyCompatibilityEntry]:
    descriptor_tuple = tuple(descriptors)
    profile = resolve_location_profile(
        protocol_version=protocol_version,
        descriptors=descriptor_tuple,
        discovery_complete=True,
    )
    compatibility = compatibility_for(identity, profile)
    if compatibility.confidence is SonySupportConfidence.UNSUPPORTED and profile.executable:
        profile = resolve_location_profile(
            protocol_version=protocol_version,
            descriptors=descriptor_tuple,
            discovery_complete=True,
            registry_confidence=SonySupportConfidence.UNSUPPORTED,
        )
    return profile, compatibility


def compatibility_for(
    identity: SonyIdentity,
    profile: SonyLocationProfile,
) -> SonyCompatibilityEntry:
    if not profile.executable:
        return SonyCompatibilityEntry(
            model=identity.model,
            firmware=identity.firmware,
            protocol_version=identity.protocol_version,
            profile=profile.kind,
            confidence=SonySupportConfidence.UNSUPPORTED,
            evidence=None,
        )
    candidates = (*UNSUPPORTED_COMPATIBILITY, *VERIFIED_COMPATIBILITY)
    for entry in candidates:
        if identity.firmware is None or normalize_model(entry.model) != identity.normalized_model:
            continue
        if entry.firmware != identity.firmware:
            continue
        if entry.protocol_version != identity.protocol_version:
            continue
        if entry.profile is not profile.kind:
            continue
        return entry
    return SonyCompatibilityEntry(
        model=identity.model,
        firmware=identity.firmware,
        protocol_version=identity.protocol_version,
        profile=profile.kind,
        confidence=SonySupportConfidence.EXPERIMENTAL,
        evidence=None,
    )


def resolve_location_profile(
    *,
    protocol_version: int | None,
    descriptors: tuple[SonyGattDescriptor, ...] | list[SonyGattDescriptor],
    discovery_complete: bool,
    registry_confidence: SonySupportConfidence | None = None,
) -> SonyLocationProfile:
    if not discovery_complete:
        return _unsupported(protocol_version, "Sony service discovery is incomplete.")
    if registry_confidence is SonySupportConfidence.UNSUPPORTED:
        return _unsupported(protocol_version, "This exact identity is blocked by the compatibility registry.")

    by_uuid: dict[str, SonyGattDescriptor] = {}
    for descriptor in descriptors:
        if descriptor.service_uuid != LOCATION_SERVICE_UUID.lower():
            continue
        if descriptor.characteristic_uuid in by_uuid:
            return _unsupported(
                protocol_version,
                "Duplicate characteristic UUIDs make the Sony location service ambiguous.",
            )
        by_uuid[descriptor.characteristic_uuid] = descriptor
    core_error = _validate_core_shape(by_uuid)
    if core_error is not None:
        return _unsupported(protocol_version, core_error)

    controls = _control_shape(by_uuid)
    if controls[2] is not None:
        return _unsupported(protocol_version, controls[2])
    dd30_present, dd31_present, _ = controls
    optional = _optional_capabilities(by_uuid)
    return _resolve_version_shape(
        protocol_version=protocol_version,
        dd30_present=dd30_present,
        dd31_present=dd31_present,
        optional=optional,
    )


def _has_property(
    by_uuid: dict[str, SonyGattDescriptor],
    uuid: str,
    property_name: str,
) -> bool:
    descriptor = by_uuid.get(uuid.lower())
    return descriptor is not None and property_name in descriptor.properties


def _validate_core_shape(by_uuid: dict[str, SonyGattDescriptor]) -> str | None:
    if not _has_property(by_uuid, LOCATION_DATA_WRITE_UUID, "write"):
        if LOCATION_DATA_WRITE_UUID.lower() in by_uuid:
            return "DD11 does not support write-with-response."
        return "DD11 is missing from the Sony location service."
    if not _has_property(by_uuid, LOCATION_CONFIG_READ_UUID, "read"):
        if LOCATION_CONFIG_READ_UUID.lower() in by_uuid:
            return "DD21 is not readable."
        return "DD21 is missing from the Sony location service."
    return None


def _control_shape(
    by_uuid: dict[str, SonyGattDescriptor],
) -> tuple[bool, bool, str | None]:
    dd30_present = LOCATION_LOCK_UUID.lower() in by_uuid
    dd31_present = LOCATION_ENABLE_UUID.lower() in by_uuid
    if dd30_present != dd31_present:
        return dd30_present, dd31_present, "Only one of DD30/DD31 is present."
    if dd30_present and not (
        _has_property(by_uuid, LOCATION_LOCK_UUID, "write") and _has_property(by_uuid, LOCATION_ENABLE_UUID, "write")
    ):
        return dd30_present, dd31_present, "DD30/DD31 must both support write-with-response."
    return dd30_present, dd31_present, None


def _optional_capabilities(by_uuid: dict[str, SonyGattDescriptor]) -> dict[str, bool]:
    return {
        "has_status_notifications": _has_property(by_uuid, LOCATION_STATUS_NOTIFY_UUID, "notify")
        or _has_property(by_uuid, LOCATION_STATUS_NOTIFY_UUID, "indicate"),
        "has_time_correction": _has_property(by_uuid, TIME_CORRECTION_UUID, "read"),
        "has_area_adjustment": _has_property(by_uuid, AREA_ADJUSTMENT_UUID, "read"),
    }


def _resolve_version_shape(
    *,
    protocol_version: int | None,
    dd30_present: bool,
    dd31_present: bool,
    optional: dict[str, bool],
) -> SonyLocationProfile:
    has_controls = dd30_present and dd31_present
    if protocol_version is None:
        if has_controls:
            return SonyLocationProfile(
                kind=SonyLocationProfileKind.MODERN,
                reason="Complete modern shape with unknown protocol version; explicit approval is required.",
                protocol_version=None,
                experimental=True,
                **optional,
            )
        return _unsupported(None, "Unknown-version cameras with only the legacy shape are not executable.")
    if protocol_version >= 65:
        if not has_controls:
            return _unsupported(protocol_version, "Protocol >= 65 requires writable DD30 and DD31 controls.")
        return SonyLocationProfile(
            kind=SonyLocationProfileKind.MODERN,
            reason="Protocol >= 65 and complete modern location shape.",
            protocol_version=protocol_version,
            experimental=False,
            **optional,
        )
    if has_controls:
        return _unsupported(
            protocol_version,
            "Protocol < 65 unexpectedly exposes modern controls; model-specific evidence is required.",
        )
    return SonyLocationProfile(
        kind=SonyLocationProfileKind.LEGACY,
        reason="Protocol < 65 with complete DD11/DD21 legacy shape and no modern controls.",
        protocol_version=protocol_version,
        experimental=False,
        **optional,
    )


def parse_dd21_mode(payload: bytes) -> SonyDD21Mode:
    if len(payload) not in (6, 7):
        msg = f"DD21 must be exactly 6 or 7 bytes; received {len(payload)}."
        raise ValueError(msg)
    if payload[:4] != bytes.fromhex("06 10 00 9c"):
        raise ValueError("DD21 has an unsupported framing prefix.")
    if payload[4] & ~0x02:
        raise ValueError("DD21 contains unknown feature flag bits.")
    if any(payload[5:]):
        raise ValueError("DD21 contains non-zero reserved bytes.")
    include_timezone = bool(payload[4] & 0x02)
    return SonyDD21Mode(
        include_timezone=include_timezone,
        packet_size=95 if include_timezone else 91,
        value_hex=payload.hex(" "),
    )


def descriptors_from_services(services: Iterable[GattServiceLike]) -> tuple[SonyGattDescriptor, ...]:
    descriptors: list[SonyGattDescriptor] = []
    for service in services:
        descriptors.extend(
            SonyGattDescriptor.create(
                service_uuid=str(service.uuid),
                characteristic_uuid=str(characteristic.uuid),
                properties=tuple(characteristic.properties),
            )
            for characteristic in service.characteristics
        )
    return tuple(descriptors)


def identity_characteristic_uuids() -> tuple[str, str]:
    return FIRMWARE_VERSION_UUID, CAMERA_MODEL_UUID


def approved_snapshot_uuids() -> frozenset[str]:
    return frozenset(
        {
            FIRMWARE_VERSION_UUID,
            CAMERA_MODEL_UUID,
            LOCATION_STATUS_NOTIFY_UUID.lower(),
            LOCATION_DATA_WRITE_UUID.lower(),
            LOCATION_CONFIG_READ_UUID.lower(),
            LOCATION_LOCK_UUID.lower(),
            LOCATION_ENABLE_UUID.lower(),
            TIME_CORRECTION_UUID.lower(),
            AREA_ADJUSTMENT_UUID.lower(),
        }
    )


def expected_identity_service(uuid: str) -> str:
    if uuid.lower() in {FIRMWARE_VERSION_UUID, CAMERA_MODEL_UUID}:
        return CAMERA_CONTROL_SERVICE_UUID.lower()
    return LOCATION_SERVICE_UUID.lower()


def _unsupported(protocol_version: int | None, reason: str) -> SonyLocationProfile:
    return SonyLocationProfile(
        kind=SonyLocationProfileKind.UNSUPPORTED,
        reason=reason,
        protocol_version=protocol_version,
        experimental=False,
    )
