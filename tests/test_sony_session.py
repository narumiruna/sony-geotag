from __future__ import annotations

import pytest

from sonygeotag.sony_capabilities import SonyLocationProfile
from sonygeotag.sony_capabilities import SonyLocationProfileKind
from sonygeotag.sony_protocol import LOCATION_CONFIG_READ_UUID
from sonygeotag.sony_protocol import LOCATION_ENABLE_UUID
from sonygeotag.sony_protocol import LOCATION_LOCK_UUID
from sonygeotag.sony_session import SonySessionAction
from sonygeotag.sony_session import SonySessionActionKind
from sonygeotag.sony_session import compensation_actions
from sonygeotag.sony_session import create_session_plan


@pytest.mark.parametrize("optional_capabilities", [False, True])
def test_modern_plan_cleanup_matches_full_compensation(optional_capabilities) -> None:
    profile = SonyLocationProfile(
        kind=SonyLocationProfileKind.MODERN,
        reason="fixture",
        protocol_version=101,
        experimental=False,
        has_status_notifications=optional_capabilities,
        has_time_correction=optional_capabilities,
        has_area_adjustment=optional_capabilities,
    )

    plan = create_session_plan(profile)

    assert plan.profile is SonyLocationProfileKind.MODERN
    assert plan.cleanup == compensation_actions(dd30_acquired=True, dd31_acquired=True)
    assert plan.cleanup == (
        SonySessionAction("write_dd31_disable", SonySessionActionKind.WRITE, LOCATION_ENABLE_UUID, b"\x00"),
        SonySessionAction("write_dd30_unlock", SonySessionActionKind.WRITE, LOCATION_LOCK_UUID, b"\x00"),
    )


@pytest.mark.parametrize("kind", [SonyLocationProfileKind.LEGACY, SonyLocationProfileKind.UNSUPPORTED])
def test_legacy_and_unsupported_plans_never_compensate_controls(kind) -> None:
    profile = SonyLocationProfile(kind=kind, reason="fixture", protocol_version=64, experimental=False)

    plan = create_session_plan(profile)

    assert plan.profile is kind
    assert plan.cleanup == ()
    assert plan.setup == (
        (SonySessionAction("read_dd21_config", SonySessionActionKind.READ, LOCATION_CONFIG_READ_UUID, required=True),)
        if kind is SonyLocationProfileKind.LEGACY
        else ()
    )


@pytest.mark.parametrize(
    ("dd30_acquired", "dd31_acquired", "expected_names"),
    [
        (False, False, []),
        (True, False, ["write_dd30_unlock"]),
        (False, True, ["write_dd31_disable"]),
        (True, True, ["write_dd31_disable", "write_dd30_unlock"]),
    ],
)
def test_compensation_is_selected_only_for_possibly_acquired_controls(
    dd30_acquired, dd31_acquired, expected_names
) -> None:
    actions = compensation_actions(dd30_acquired=dd30_acquired, dd31_acquired=dd31_acquired)

    assert [action.name for action in actions] == expected_names
    assert all(action.value == b"\x00" and action.kind is SonySessionActionKind.WRITE for action in actions)
