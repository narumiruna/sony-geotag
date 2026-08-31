from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import UTC
from datetime import datetime
from datetime import timedelta
from datetime import timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo
from zoneinfo import ZoneInfoNotFoundError

from PIL import ExifTags
from PIL import Image
from pillow_heif import register_heif_opener

COORDINATE_TOLERANCE = 0.0001
SUPPORTED_IMAGE_FORMATS = frozenset({"HEIF", "JPEG"})

register_heif_opener()


class ExifVerificationError(ValueError):
    """An image does not provide unambiguous, matching post-write GPS evidence."""


@dataclass(frozen=True)
class ExifVerification:
    latitude: float
    longitude: float
    capture_time: datetime
    capture_time_source: str
    expected_latitude: float
    expected_longitude: float
    not_before: datetime
    image_format: str
    tolerance: float = COORDINATE_TOLERANCE

    def to_dict(self) -> dict[str, Any]:
        return {
            "verified": True,
            "image_format": self.image_format,
            "latitude": self.latitude,
            "longitude": self.longitude,
            "capture_time": self.capture_time.isoformat(),
            "capture_time_source": self.capture_time_source,
            "expected_latitude": self.expected_latitude,
            "expected_longitude": self.expected_longitude,
            "not_before": self.not_before.isoformat(),
            "coordinate_tolerance_degrees": self.tolerance,
        }


def verify_image_exif(
    *,
    photo: Path,
    expected_latitude: float,
    expected_longitude: float,
    not_before: datetime,
    camera_timezone: str | None = None,
) -> ExifVerification:
    if not_before.tzinfo is None or not_before.utcoffset() is None:
        raise ExifVerificationError("The DD11 not-before timestamp must include a UTC offset.")
    _validate_expected_coordinate(expected_latitude, -90, 90, "latitude")
    _validate_expected_coordinate(expected_longitude, -180, 180, "longitude")
    try:
        with Image.open(photo) as image:
            image_format = image.format
            if image_format not in SUPPORTED_IMAGE_FORMATS:
                supported = ", ".join(sorted(SUPPORTED_IMAGE_FORMATS))
                raise ExifVerificationError(f"The evidence image format must be one of: {supported}.")
            exif = image.getexif()
            gps = exif.get_ifd(ExifTags.IFD.GPSInfo)
    except (OSError, SyntaxError) as error:
        msg = f"Could not read image EXIF: {error}"
        raise ExifVerificationError(msg) from error

    latitude = _coordinate(gps, ExifTags.GPS.GPSLatitude, ExifTags.GPS.GPSLatitudeRef, "latitude")
    longitude = _coordinate(gps, ExifTags.GPS.GPSLongitude, ExifTags.GPS.GPSLongitudeRef, "longitude")
    if abs(latitude - expected_latitude) > COORDINATE_TOLERANCE:
        raise ExifVerificationError(
            f"GPS latitude differs by more than {COORDINATE_TOLERANCE}°: {latitude} vs {expected_latitude}."
        )
    if abs(longitude - expected_longitude) > COORDINATE_TOLERANCE:
        raise ExifVerificationError(
            f"GPS longitude differs by more than {COORDINATE_TOLERANCE}°: {longitude} vs {expected_longitude}."
        )

    capture_time, source = _capture_time(exif, gps, camera_timezone)
    if capture_time <= not_before:
        raise ExifVerificationError(
            f"Photo capture time {capture_time.isoformat()} is not strictly later than DD11 {not_before.isoformat()}."
        )
    return ExifVerification(
        latitude=latitude,
        longitude=longitude,
        capture_time=capture_time,
        capture_time_source=source,
        expected_latitude=expected_latitude,
        expected_longitude=expected_longitude,
        not_before=not_before,
        image_format=image_format,
    )


verify_jpeg_exif = verify_image_exif


def parse_iso_datetime(value: str) -> datetime:
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as error:
        msg = f"Invalid ISO-8601 timestamp: {value}"
        raise ExifVerificationError(msg) from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ExifVerificationError("ISO-8601 timestamp must include a UTC offset.")
    return parsed


def _coordinate(gps: dict[int, Any], value_tag: int, ref_tag: int, label: str) -> float:
    values = gps.get(value_tag)
    reference = gps.get(ref_tag)
    if not isinstance(values, (tuple, list)) or len(values) != 3 or not isinstance(reference, str):
        raise ExifVerificationError(f"Image is missing complete GPS {label} EXIF.")
    try:
        degrees, minutes, seconds = (float(value) for value in values)
    except (TypeError, ValueError, ZeroDivisionError) as error:
        msg = f"Image has malformed GPS {label} EXIF."
        raise ExifVerificationError(msg) from error
    if not all(math.isfinite(value) for value in (degrees, minutes, seconds)):
        raise ExifVerificationError(f"Image has non-finite GPS {label} EXIF.")
    maximum_degrees = 90 if label == "latitude" else 180
    if not 0 <= degrees <= maximum_degrees or not 0 <= minutes < 60 or not 0 <= seconds < 60:
        raise ExifVerificationError(f"Image has out-of-range GPS {label} EXIF.")
    if degrees == maximum_degrees and (minutes != 0 or seconds != 0):
        raise ExifVerificationError(f"Image has out-of-range GPS {label} EXIF.")
    coordinate = degrees + minutes / 60 + seconds / 3600
    reference = reference.upper()
    if label == "latitude" and reference not in {"N", "S"}:
        raise ExifVerificationError("Image has an invalid GPS latitude reference.")
    if label == "longitude" and reference not in {"E", "W"}:
        raise ExifVerificationError("Image has an invalid GPS longitude reference.")
    if reference in {"S", "W"}:
        coordinate = -coordinate
    return coordinate


def _capture_time(
    exif: Image.Exif,
    gps: dict[int, Any],
    camera_timezone: str | None,
) -> tuple[datetime, str]:
    local_error: ExifVerificationError | None = None
    for candidate in (_exif_details(exif), exif):
        try:
            return _local_capture_time(candidate, camera_timezone)
        except ExifVerificationError as error:
            local_error = error
    gps_capture = _gps_capture_time(gps)
    if gps_capture is not None:
        return gps_capture, "GPS UTC"
    if local_error is not None:
        raise local_error
    raise ExifVerificationError("Image has no usable capture time.")


def _exif_details(exif: Image.Exif) -> Image.Exif | dict[int, Any]:
    try:
        return exif.get_ifd(ExifTags.IFD.Exif)
    except (KeyError, TypeError):
        return {}


def _gps_capture_time(gps: dict[int, Any]) -> datetime | None:
    gps_date = gps.get(ExifTags.GPS.GPSDateStamp)
    gps_time = gps.get(ExifTags.GPS.GPSTimeStamp)
    if not isinstance(gps_date, str) or not isinstance(gps_time, (tuple, list)) or len(gps_time) != 3:
        return None
    try:
        date_value = datetime.strptime(f"{gps_date} +0000", "%Y:%m:%d %z").date()
        hours, minutes, seconds = (float(value) for value in gps_time)
        if not all(math.isfinite(value) for value in (hours, minutes, seconds)):
            return None
        if not hours.is_integer() or not minutes.is_integer():
            return None
        if not 0 <= hours < 24 or not 0 <= minutes < 60 or not 0 <= seconds < 60:
            return None
        midnight = datetime(date_value.year, date_value.month, date_value.day, tzinfo=UTC)
        return midnight + timedelta(
            hours=int(hours),
            minutes=int(minutes),
            microseconds=round(seconds * 1_000_000),
        )
    except (TypeError, ValueError, OverflowError, ZeroDivisionError):
        return None


def _local_capture_time(exif: Image.Exif | dict[int, Any], camera_timezone: str | None) -> tuple[datetime, str]:
    original = exif.get(ExifTags.Base.DateTimeOriginal)
    if not isinstance(original, str):
        raise ExifVerificationError("Image has no usable GPS UTC time or DateTimeOriginal.")
    try:
        local_time = datetime.strptime(f"{original} +0000", "%Y:%m:%d %H:%M:%S %z").replace(tzinfo=None)
    except ValueError as error:
        raise ExifVerificationError("Image DateTimeOriginal is malformed.") from error

    offset_value = exif.get(ExifTags.Base.OffsetTimeOriginal)
    if isinstance(offset_value, str):
        return local_time.replace(tzinfo=_parse_exif_offset(offset_value)), "EXIF original time + offset"
    if camera_timezone is None:
        raise ExifVerificationError(
            "Image time is ambiguous; provide EXIF offset data or --camera-timezone with an IANA timezone."
        )
    try:
        zone = ZoneInfo(camera_timezone)
    except ZoneInfoNotFoundError as error:
        msg = f"Unknown IANA camera timezone: {camera_timezone}"
        raise ExifVerificationError(msg) from error
    return _unambiguous_zoned_time(local_time, zone), "EXIF original time + explicit camera timezone"


def _validate_expected_coordinate(value: float, lower: float, upper: float, label: str) -> None:
    if not math.isfinite(value) or not lower <= value <= upper:
        raise ExifVerificationError(f"Expected {label} must be finite and within {lower}..{upper}.")


def _unambiguous_zoned_time(local_time: datetime, zone: ZoneInfo) -> datetime:
    candidates: dict[datetime, datetime] = {}
    for fold in (0, 1):
        candidate = local_time.replace(tzinfo=zone, fold=fold)
        round_trip = candidate.astimezone(UTC).astimezone(zone).replace(tzinfo=None)
        if round_trip == local_time:
            candidates[candidate.astimezone(UTC)] = candidate
    if len(candidates) != 1:
        raise ExifVerificationError("Image local capture time is ambiguous or nonexistent in the camera timezone.")
    return next(iter(candidates.values()))


def _parse_exif_offset(value: str) -> timezone:
    try:
        if re.fullmatch(r"[+-]\d{2}:\d{2}", value) is None:
            raise ValueError
        sign = -1 if value.startswith("-") else 1
        hours_text, minutes_text = value[1:].split(":", 1)
        hours = int(hours_text)
        minutes = int(minutes_text)
        if hours > 23 or minutes > 59:
            raise ValueError
        return timezone(timedelta(hours=sign * hours, minutes=sign * minutes))
    except (ValueError, IndexError):
        raise ExifVerificationError("Image OffsetTimeOriginal is malformed.") from None
