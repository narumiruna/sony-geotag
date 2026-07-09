import struct
from datetime import UTC
from datetime import datetime
from datetime import timedelta
from datetime import timezone

import pytest

from sonygeotag.sony_protocol import LOCATION_PACKET_SIZE_WITH_TIMEZONE
from sonygeotag.sony_protocol import LOCATION_PACKET_SIZE_WITHOUT_TIMEZONE
from sonygeotag.sony_protocol import PAIRING_INIT_PAYLOAD
from sonygeotag.sony_protocol import SONY_COMPANY_ID
from sonygeotag.sony_protocol import TIME_AREA_PACKET_SIZE
from sonygeotag.sony_protocol import encode_location_packet
from sonygeotag.sony_protocol import encode_pairing_init
from sonygeotag.sony_protocol import encode_time_area_packet
from sonygeotag.sony_protocol import parse_config_requires_timezone
from sonygeotag.sony_protocol import parse_sony_advertisement
from sonygeotag.sony_protocol import split_timezone_offset


def test_parse_sony_advertisement_identifies_a7c2_protocol_version() -> None:
    payload = bytes.fromhex("03 00 65 00 55 31 22 bf c0 23 b7 ac 21 60 00 00 00 00 00 00")

    info = parse_sony_advertisement({SONY_COMPANY_ID: payload})

    assert info is not None
    assert info.is_camera is True
    assert info.protocol_version == 0x65
    assert info.requires_unlock is True


def test_parse_config_requires_timezone_checks_byte_4_bit_1() -> None:
    assert parse_config_requires_timezone(bytes.fromhex("00 00 00 00 02"))
    assert not parse_config_requires_timezone(bytes.fromhex("00 00 00 00 00"))
    assert not parse_config_requires_timezone(bytes.fromhex("00 00"))


def test_encode_pairing_init_matches_sony_vendor_pairing_payload() -> None:
    assert encode_pairing_init() == PAIRING_INIT_PAYLOAD
    assert encode_pairing_init() == bytes.fromhex("06 08 01 00 00 00 00")


def test_encode_location_packet_with_timezone_uses_dd11_big_endian_format() -> None:
    date_time = datetime(2026, 7, 9, 20, 34, 56, tzinfo=timezone(timedelta(hours=3)))
    packet = encode_location_packet(
        latitude=40.614380674810334,
        longitude=22.971624208899676,
        date_time=date_time,
        include_timezone=True,
    )

    assert len(packet) == LOCATION_PACKET_SIZE_WITH_TIMEZONE
    assert packet[0:2] == bytes.fromhex("00 5d")
    assert packet[2:6] == bytes.fromhex("08 02 fc 03")
    assert packet[6:11] == bytes.fromhex("00 00 10 10 10")
    assert struct.unpack(">i", packet[11:15])[0] == int(40.614380674810334 * 10_000_000)
    assert struct.unpack(">i", packet[15:19])[0] == int(22.971624208899676 * 10_000_000)
    assert packet[19:26] == bytes.fromhex("07 ea 07 09 11 22 38")
    assert packet[26:91] == bytes(65)
    assert struct.unpack(">h", packet[91:93])[0] == 180
    assert struct.unpack(">h", packet[93:95])[0] == 0


def test_encode_location_packet_without_timezone_uses_91_byte_format() -> None:
    date_time = datetime(2026, 7, 9, 20, 34, 56, tzinfo=UTC)
    packet = encode_location_packet(latitude=-33.5, longitude=151.2, date_time=date_time, include_timezone=False)

    assert len(packet) == LOCATION_PACKET_SIZE_WITHOUT_TIMEZONE
    assert packet[0:2] == bytes.fromhex("00 59")
    assert packet[2:6] == bytes.fromhex("08 02 fc 00")
    assert struct.unpack(">i", packet[11:15])[0] == -335000000
    assert struct.unpack(">i", packet[15:19])[0] == 1512000000
    assert packet[19:26] == bytes.fromhex("07 ea 07 09 14 22 38")


def test_encode_time_area_packet_uses_cc13_local_time_format() -> None:
    date_time = datetime(2026, 7, 9, 20, 34, 56, tzinfo=timezone(timedelta(hours=9)))

    packet = encode_time_area_packet(date_time)

    assert len(packet) == TIME_AREA_PACKET_SIZE
    assert packet == bytes.fromhex("0c 00 00 07 ea 07 09 14 22 38 00 09 00")


def test_split_timezone_offset_uses_signed_hours_and_unsigned_minutes() -> None:
    assert split_timezone_offset(9 * 3600) == (9, 0)
    assert split_timezone_offset(-(5 * 3600 + 30 * 60)) == (-5, 30)
    assert split_timezone_offset(-(30 * 60)) == (0, 30)


def test_encode_location_packet_rejects_invalid_coordinates() -> None:
    with pytest.raises(ValueError, match="latitude out of range"):
        encode_location_packet(latitude=91, longitude=0)
    with pytest.raises(ValueError, match="longitude out of range"):
        encode_location_packet(latitude=0, longitude=181)
