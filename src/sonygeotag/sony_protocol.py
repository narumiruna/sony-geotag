from __future__ import annotations

import struct
from dataclasses import dataclass
from datetime import UTC
from datetime import datetime
from datetime import timedelta

SONY_COMPANY_ID = 0x012D
SONY_CAMERA_DEVICE_TYPE = 0x0003
PROTOCOL_VERSION_REQUIRES_UNLOCK = 65
COORDINATE_SCALE = 10_000_000

REMOTE_CONTROL_SERVICE_UUID = "8000ff00-ff00-ffff-ffff-ffffffffffff"
CAMERA_CONTROL_SERVICE_UUID = "8000cc00-cc00-ffff-ffff-ffffffffffff"
LOCATION_SERVICE_UUID = "8000dd00-dd00-ffff-ffff-ffffffffffff"
PAIRING_SERVICE_UUID = "8000ee00-ee00-ffff-ffff-ffffffffffff"

DATETIME_NOTIFY_UUID = "0000cc0e-0000-1000-8000-00805f9b34fb"
TIME_COMPLETION_STATUS_UUID = "0000cc09-0000-1000-8000-00805f9b34fb"
DATE_FORMAT_UUID = "0000cc12-0000-1000-8000-00805f9b34fb"
TIME_AREA_SETTING_UUID = "0000cc13-0000-1000-8000-00805f9b34fb"

LOCATION_STATUS_NOTIFY_UUID = "0000dd01-0000-1000-8000-00805f9b34fb"
LOCATION_DATA_WRITE_UUID = "0000dd11-0000-1000-8000-00805f9b34fb"
LOCATION_CONFIG_READ_UUID = "0000dd21-0000-1000-8000-00805f9b34fb"
LOCATION_LOCK_UUID = "0000dd30-0000-1000-8000-00805f9b34fb"
LOCATION_ENABLE_UUID = "0000dd31-0000-1000-8000-00805f9b34fb"
TIME_CORRECTION_UUID = "0000dd32-0000-1000-8000-00805f9b34fb"
AREA_ADJUSTMENT_UUID = "0000dd33-0000-1000-8000-00805f9b34fb"

PAIRING_INIT_UUID = "0000ee01-0000-1000-8000-00805f9b34fb"
PAIRING_INIT_PAYLOAD = bytes.fromhex("06 08 01 00 00 00 00")

LOCATION_PACKET_SIZE_WITHOUT_TIMEZONE = 91
LOCATION_PACKET_SIZE_WITH_TIMEZONE = 95
TIME_AREA_PACKET_SIZE = 13


@dataclass(frozen=True)
class SonyAdvertisementInfo:
    is_camera: bool
    protocol_version: int | None
    requires_unlock: bool | None

    def to_dict(self) -> dict[str, bool | int | None]:
        return {
            "is_camera": self.is_camera,
            "protocol_version": self.protocol_version,
            "requires_unlock": self.requires_unlock,
        }


def parse_sony_advertisement(manufacturer_data: dict[int, bytes]) -> SonyAdvertisementInfo | None:
    payload = manufacturer_data.get(SONY_COMPANY_ID)
    if payload is None:
        return None

    device_type = parse_sony_device_type(payload)
    protocol_version = parse_sony_protocol_version(payload)
    return SonyAdvertisementInfo(
        is_camera=device_type == SONY_CAMERA_DEVICE_TYPE,
        protocol_version=protocol_version,
        requires_unlock=requires_protocol_unlock(protocol_version),
    )


def parse_sony_device_type(payload: bytes) -> int | None:
    if len(payload) < 2:
        return None
    return int.from_bytes(payload[0:2], byteorder="little", signed=False)


def parse_sony_protocol_version(payload: bytes) -> int | None:
    if len(payload) < 4:
        return None
    return payload[2]


def requires_protocol_unlock(protocol_version: int | None) -> bool | None:
    if protocol_version is None:
        return None
    return protocol_version >= PROTOCOL_VERSION_REQUIRES_UNLOCK


def parse_config_requires_timezone(payload: bytes) -> bool:
    if len(payload) < 5:
        return False
    return (payload[4] & 0x02) == 0x02


def encode_pairing_init() -> bytes:
    return PAIRING_INIT_PAYLOAD


def encode_location_packet(
    latitude: float,
    longitude: float,
    date_time: datetime | None = None,
    include_timezone: bool = True,
) -> bytes:
    """Encode a Sony DD11 location packet.

    DD11 stores the timestamp in UTC. If timezone data is included, the final four bytes contain
    standard timezone offset minutes and DST savings minutes as signed big-endian int16 values.
    """
    validate_coordinates(latitude=latitude, longitude=longitude)
    local_datetime = normalize_datetime(date_time)
    utc_datetime = local_datetime.astimezone(UTC)
    packet_size = LOCATION_PACKET_SIZE_WITH_TIMEZONE if include_timezone else LOCATION_PACKET_SIZE_WITHOUT_TIMEZONE
    payload_size = packet_size - 2
    packet = bytearray(packet_size)

    struct.pack_into(">H", packet, 0, payload_size)
    packet[2:5] = b"\x08\x02\xfc"
    packet[5] = 0x03 if include_timezone else 0x00
    packet[8:11] = b"\x10\x10\x10"

    struct.pack_into(">i", packet, 11, int(latitude * COORDINATE_SCALE))
    struct.pack_into(">i", packet, 15, int(longitude * COORDINATE_SCALE))
    struct.pack_into(">H", packet, 19, utc_datetime.year)
    packet[21] = utc_datetime.month
    packet[22] = utc_datetime.day
    packet[23] = utc_datetime.hour
    packet[24] = utc_datetime.minute
    packet[25] = utc_datetime.second

    if include_timezone:
        standard_offset, dst_offset = timezone_offsets(local_datetime)
        struct.pack_into(">h", packet, 91, minutes_from_timedelta(standard_offset))
        struct.pack_into(">h", packet, 93, minutes_from_timedelta(dst_offset))

    return bytes(packet)


def encode_time_area_packet(date_time: datetime | None = None) -> bytes:
    """Encode a Sony CC13 date/time packet using local time components."""
    local_datetime = normalize_datetime(date_time)
    actual_offset = local_datetime.utcoffset() or timedelta()
    dst_offset = local_datetime.dst() or timedelta()
    total_offset_seconds = int(actual_offset.total_seconds())
    offset_hours, offset_minutes = split_timezone_offset(total_offset_seconds)

    packet = bytearray(TIME_AREA_PACKET_SIZE)
    packet[0:3] = b"\x0c\x00\x00"
    struct.pack_into(">H", packet, 3, local_datetime.year)
    packet[5] = local_datetime.month
    packet[6] = local_datetime.day
    packet[7] = local_datetime.hour
    packet[8] = local_datetime.minute
    packet[9] = local_datetime.second
    packet[10] = 0x01 if dst_offset != timedelta() else 0x00
    struct.pack_into("b", packet, 11, offset_hours)
    packet[12] = offset_minutes
    return bytes(packet)


def validate_coordinates(latitude: float, longitude: float) -> None:
    if not -90 <= latitude <= 90:
        msg = f"latitude out of range: {latitude}"
        raise ValueError(msg)
    if not -180 <= longitude <= 180:
        msg = f"longitude out of range: {longitude}"
        raise ValueError(msg)


def normalize_datetime(date_time: datetime | None) -> datetime:
    if date_time is None:
        return datetime.now().astimezone()
    if date_time.tzinfo is None:
        return date_time.astimezone()
    return date_time


def timezone_offsets(date_time: datetime) -> tuple[timedelta, timedelta]:
    actual_offset = date_time.utcoffset() or timedelta()
    dst_offset = date_time.dst() or timedelta()
    standard_offset = actual_offset - dst_offset
    return standard_offset, dst_offset


def minutes_from_timedelta(delta: timedelta) -> int:
    return int(delta.total_seconds() / 60)


def split_timezone_offset(total_offset_seconds: int) -> tuple[int, int]:
    sign = -1 if total_offset_seconds < 0 else 1
    absolute_seconds = abs(total_offset_seconds)
    hours = sign * (absolute_seconds // 3600)
    minutes = (absolute_seconds % 3600) // 60
    return hours, minutes
