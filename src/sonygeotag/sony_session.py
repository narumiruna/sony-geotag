from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from sonygeotag.sony_capabilities import SonyLocationProfile
from sonygeotag.sony_capabilities import SonyLocationProfileKind
from sonygeotag.sony_protocol import AREA_ADJUSTMENT_UUID
from sonygeotag.sony_protocol import LOCATION_CONFIG_READ_UUID
from sonygeotag.sony_protocol import LOCATION_ENABLE_UUID
from sonygeotag.sony_protocol import LOCATION_LOCK_UUID
from sonygeotag.sony_protocol import LOCATION_STATUS_NOTIFY_UUID
from sonygeotag.sony_protocol import TIME_CORRECTION_UUID


class SonySessionActionKind(StrEnum):
    NOTIFY = "notify"
    WRITE = "write"
    READ = "read"


@dataclass(frozen=True)
class SonySessionAction:
    name: str
    kind: SonySessionActionKind
    uuid: str
    value: bytes | None = None
    required: bool = False


@dataclass(frozen=True)
class SonyLocationSessionPlan:
    profile: SonyLocationProfileKind
    setup: tuple[SonySessionAction, ...]
    cleanup: tuple[SonySessionAction, ...]


def create_session_plan(profile: SonyLocationProfile) -> SonyLocationSessionPlan:
    if not profile.executable:
        return SonyLocationSessionPlan(profile=SonyLocationProfileKind.UNSUPPORTED, setup=(), cleanup=())

    if profile.kind is SonyLocationProfileKind.LEGACY:
        return SonyLocationSessionPlan(
            profile=profile.kind,
            setup=(
                SonySessionAction(
                    name="read_dd21_config",
                    kind=SonySessionActionKind.READ,
                    uuid=LOCATION_CONFIG_READ_UUID,
                    required=True,
                ),
            ),
            cleanup=(),
        )

    setup: list[SonySessionAction] = []
    if profile.has_status_notifications:
        setup.append(
            SonySessionAction(
                name="start_dd01_notify",
                kind=SonySessionActionKind.NOTIFY,
                uuid=LOCATION_STATUS_NOTIFY_UUID,
                value=b"\x01",
            )
        )
    setup.extend(
        (
            SonySessionAction(
                name="write_dd30_lock",
                kind=SonySessionActionKind.WRITE,
                uuid=LOCATION_LOCK_UUID,
                value=b"\x01",
                required=True,
            ),
            SonySessionAction(
                name="write_dd31_enable",
                kind=SonySessionActionKind.WRITE,
                uuid=LOCATION_ENABLE_UUID,
                value=b"\x01",
                required=True,
            ),
        )
    )
    if profile.has_time_correction:
        setup.append(
            SonySessionAction(
                name="read_dd32_time_correction",
                kind=SonySessionActionKind.READ,
                uuid=TIME_CORRECTION_UUID,
            )
        )
    if profile.has_area_adjustment:
        setup.append(
            SonySessionAction(
                name="read_dd33_area_adjustment",
                kind=SonySessionActionKind.READ,
                uuid=AREA_ADJUSTMENT_UUID,
            )
        )
    setup.append(
        SonySessionAction(
            name="read_dd21_config",
            kind=SonySessionActionKind.READ,
            uuid=LOCATION_CONFIG_READ_UUID,
            required=True,
        )
    )
    return SonyLocationSessionPlan(
        profile=profile.kind,
        setup=tuple(setup),
        cleanup=(
            SonySessionAction(
                name="write_dd31_disable",
                kind=SonySessionActionKind.WRITE,
                uuid=LOCATION_ENABLE_UUID,
                value=b"\x00",
            ),
            SonySessionAction(
                name="write_dd30_unlock",
                kind=SonySessionActionKind.WRITE,
                uuid=LOCATION_LOCK_UUID,
                value=b"\x00",
            ),
        ),
    )


def compensation_actions(*, dd30_acquired: bool, dd31_acquired: bool) -> tuple[SonySessionAction, ...]:
    actions: list[SonySessionAction] = []
    if dd31_acquired:
        actions.append(
            SonySessionAction(
                name="write_dd31_disable",
                kind=SonySessionActionKind.WRITE,
                uuid=LOCATION_ENABLE_UUID,
                value=b"\x00",
            )
        )
    if dd30_acquired:
        actions.append(
            SonySessionAction(
                name="write_dd30_unlock",
                kind=SonySessionActionKind.WRITE,
                uuid=LOCATION_LOCK_UUID,
                value=b"\x00",
            )
        )
    return tuple(actions)
