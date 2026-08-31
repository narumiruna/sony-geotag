from __future__ import annotations

from datetime import UTC
from datetime import datetime
from pathlib import Path

import pytest
from PIL import ExifTags
from PIL import Image

from sonygeotag.exif_verify import ExifVerification
from sonygeotag.exif_verify import ExifVerificationError
from sonygeotag.exif_verify import parse_iso_datetime
from sonygeotag.exif_verify import verify_image_exif
from sonygeotag.exif_verify import verify_jpeg_exif


class FakeExif(dict[int, object]):
    def __init__(
        self,
        gps: dict[int, object],
        values: dict[int, object] | None = None,
        exif_details: dict[int, object] | None = None,
    ) -> None:
        super().__init__(values or {})
        self.gps = gps
        self.exif_details = exif_details or {}

    def get_ifd(self, tag: object) -> dict[int, object]:
        return self.gps if tag == ExifTags.IFD.GPSInfo else self.exif_details


class FakeImage:
    def __init__(self, exif: FakeExif, image_format: str = "JPEG") -> None:
        self.exif = exif
        self.format = image_format

    def __enter__(self) -> FakeImage:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def getexif(self) -> FakeExif:
        return self.exif


def gps(*, latitude: float = 35.0, longitude: float = 139.0, capture_second: int = 2) -> dict[int, object]:
    return {
        ExifTags.GPS.GPSLatitude: (latitude, 0, 0),
        ExifTags.GPS.GPSLatitudeRef: "N",
        ExifTags.GPS.GPSLongitude: (longitude, 0, 0),
        ExifTags.GPS.GPSLongitudeRef: "E",
        ExifTags.GPS.GPSDateStamp: "2026:08:09",
        ExifTags.GPS.GPSTimeStamp: (0, 0, capture_second),
    }


def patch_image(monkeypatch: pytest.MonkeyPatch, exif: FakeExif, image_format: str = "JPEG") -> None:
    monkeypatch.setattr(Image, "open", lambda _path: FakeImage(exif, image_format))


def verify() -> ExifVerification:
    return verify_jpeg_exif(
        photo=Path("evidence.jpg"),
        expected_latitude=35.0,
        expected_longitude=139.0,
        not_before=datetime(2026, 8, 9, 0, 0, 1, tzinfo=UTC),
    )


def test_verifier_accepts_matching_post_write_gps_utc(monkeypatch: pytest.MonkeyPatch) -> None:
    patch_image(monkeypatch, FakeExif(gps()))

    result = verify()

    assert result.capture_time_source == "GPS UTC"
    assert result.capture_time == datetime(2026, 8, 9, 0, 0, 2, tzinfo=UTC)
    assert result.to_dict()["image_format"] == "JPEG"


def test_verifier_accepts_heif_with_the_same_strict_exif_rules(monkeypatch: pytest.MonkeyPatch) -> None:
    patch_image(monkeypatch, FakeExif(gps()), image_format="HEIF")

    result = verify_image_exif(
        photo=Path("evidence.hif"),
        expected_latitude=35.0,
        expected_longitude=139.0,
        not_before=datetime(2026, 8, 9, 0, 0, 1, tzinfo=UTC),
    )

    assert result.image_format == "HEIF"
    assert result.capture_time_source == "GPS UTC"


def test_verifier_rejects_unsupported_image_format(monkeypatch: pytest.MonkeyPatch) -> None:
    patch_image(monkeypatch, FakeExif(gps()), image_format="PNG")

    with pytest.raises(ExifVerificationError, match="HEIF, JPEG"):
        verify_image_exif(
            photo=Path("evidence.png"),
            expected_latitude=35.0,
            expected_longitude=139.0,
            not_before=datetime(2026, 8, 9, 0, 0, 1, tzinfo=UTC),
        )


def test_verifier_rejects_missing_gps(monkeypatch: pytest.MonkeyPatch) -> None:
    patch_image(monkeypatch, FakeExif({}))

    with pytest.raises(ExifVerificationError, match="missing complete GPS latitude"):
        verify()


def test_verifier_rejects_out_of_tolerance_coordinate(monkeypatch: pytest.MonkeyPatch) -> None:
    patch_image(monkeypatch, FakeExif(gps(latitude=35.0002)))

    with pytest.raises(ExifVerificationError, match="differs by more"):
        verify()


def test_verifier_rejects_capture_not_strictly_after_dd11(monkeypatch: pytest.MonkeyPatch) -> None:
    patch_image(monkeypatch, FakeExif(gps(capture_second=1)))

    with pytest.raises(ExifVerificationError, match="not strictly later"):
        verify()


def test_verifier_rejects_ambiguous_local_time_without_offset(monkeypatch: pytest.MonkeyPatch) -> None:
    local_only = gps()
    local_only.pop(ExifTags.GPS.GPSDateStamp)
    local_only.pop(ExifTags.GPS.GPSTimeStamp)
    patch_image(
        monkeypatch,
        FakeExif(local_only, {ExifTags.Base.DateTimeOriginal: "2026:08:09 09:00:02"}),
    )

    with pytest.raises(ExifVerificationError, match="ambiguous"):
        verify()


def test_verifier_accepts_local_time_with_exif_offset(monkeypatch: pytest.MonkeyPatch) -> None:
    local_only = gps()
    local_only.pop(ExifTags.GPS.GPSDateStamp)
    local_only.pop(ExifTags.GPS.GPSTimeStamp)
    patch_image(
        monkeypatch,
        FakeExif(
            local_only,
            {
                ExifTags.Base.DateTimeOriginal: "2026:08:09 09:00:02",
                ExifTags.Base.OffsetTimeOriginal: "+09:00",
            },
        ),
    )

    assert verify().capture_time.astimezone(UTC) == datetime(2026, 8, 9, 0, 0, 2, tzinfo=UTC)


def test_verifier_prefers_actual_capture_time_from_nested_exif_ifd(monkeypatch: pytest.MonkeyPatch) -> None:
    patch_image(
        monkeypatch,
        FakeExif(
            gps(capture_second=2),
            exif_details={
                ExifTags.Base.DateTimeOriginal: "2026:08:09 09:00:20",
                ExifTags.Base.OffsetTimeOriginal: "+09:00",
            },
        ),
        image_format="HEIF",
    )

    result = verify()

    assert result.capture_time.astimezone(UTC) == datetime(2026, 8, 9, 0, 0, 20, tzinfo=UTC)
    assert result.capture_time_source == "EXIF original time + offset"


def test_verifier_rejects_nonfinite_and_invalid_dms(monkeypatch: pytest.MonkeyPatch) -> None:
    patch_image(monkeypatch, FakeExif(gps(latitude=float("nan"))))
    with pytest.raises(ExifVerificationError, match="non-finite"):
        verify()

    patch_image(monkeypatch, FakeExif(gps(latitude=91)))
    with pytest.raises(ExifVerificationError, match="out-of-range"):
        verify()

    patch_image(monkeypatch, FakeExif(gps()))
    with pytest.raises(ExifVerificationError, match="Expected latitude"):
        verify_jpeg_exif(
            photo=Path("evidence.jpg"),
            expected_latitude=float("nan"),
            expected_longitude=139,
            not_before=datetime(2026, 8, 9, 0, 0, 1, tzinfo=UTC),
        )


def test_verifier_normalizes_fractional_gps_second_rounding_across_midnight(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    near_midnight = gps()
    near_midnight[ExifTags.GPS.GPSTimeStamp] = (23, 59, 59.9999999)
    patch_image(monkeypatch, FakeExif(near_midnight))

    result = verify_image_exif(
        photo=Path("evidence.jpg"),
        expected_latitude=35,
        expected_longitude=139,
        not_before=datetime(2026, 8, 9, 23, 59, 59, tzinfo=UTC),
    )

    assert result.capture_time == datetime(2026, 8, 10, tzinfo=UTC)


@pytest.mark.parametrize(
    "gps_time",
    [(-0.5, 0, 2), (1.5, 0, 2), (0, 60, 2), (0, 0, 60), (float("nan"), 0, 2)],
)
def test_verifier_rejects_malformed_gps_utc_components(
    monkeypatch: pytest.MonkeyPatch,
    gps_time: tuple[float, float, float],
) -> None:
    malformed = gps()
    malformed[ExifTags.GPS.GPSTimeStamp] = gps_time
    patch_image(monkeypatch, FakeExif(malformed))

    with pytest.raises(ExifVerificationError, match="no usable GPS UTC time"):
        verify()


def test_explicit_camera_timezone_rejects_dst_overlap(monkeypatch: pytest.MonkeyPatch) -> None:
    local_only = gps()
    local_only.pop(ExifTags.GPS.GPSDateStamp)
    local_only.pop(ExifTags.GPS.GPSTimeStamp)
    patch_image(
        monkeypatch,
        FakeExif(local_only, {ExifTags.Base.DateTimeOriginal: "2026:11:01 01:30:00"}),
    )

    with pytest.raises(ExifVerificationError, match="ambiguous or nonexistent"):
        verify_jpeg_exif(
            photo=Path("evidence.jpg"),
            expected_latitude=35,
            expected_longitude=139,
            not_before=datetime(2026, 10, 31, tzinfo=UTC),
            camera_timezone="America/New_York",
        )


def test_parse_iso_timestamp_requires_offset() -> None:
    assert parse_iso_datetime("2026-08-09T00:00:01Z").tzinfo is UTC
    with pytest.raises(ExifVerificationError, match="must include"):
        parse_iso_datetime("2026-08-09T00:00:01")
