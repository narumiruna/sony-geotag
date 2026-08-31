from sonygeotag.ble_probe import DEFAULT_TARGETS
from sonygeotag.ble_probe import CharacteristicInfo
from sonygeotag.ble_probe import ObservedDevice
from sonygeotag.ble_probe import ReadValue
from sonygeotag.ble_probe import bytes_to_hex
from sonygeotag.ble_probe import manufacturer_data_to_dict
from sonygeotag.ble_probe import matches_characteristic_filters
from sonygeotag.ble_probe import matches_targets
from sonygeotag.ble_probe import normalize_characteristic_filters
from sonygeotag.ble_probe import normalize_targets
from sonygeotag.ble_probe import notification_event


def test_bytes_to_hex_spaces_bytes() -> None:
    assert bytes_to_hex(b"\x03\x00e") == "03 00 65"


def test_manufacturer_data_to_dict_uses_hex_company_ids() -> None:
    assert manufacturer_data_to_dict({0x012D: b"\x03\x00"}) == {"0x012d": "03 00"}


def test_normalize_targets_defaults_to_a7c2_names() -> None:
    assert normalize_targets(None) == DEFAULT_TARGETS
    assert normalize_targets([]) == DEFAULT_TARGETS


def test_normalize_characteristic_filters_removes_empty_values() -> None:
    assert normalize_characteristic_filters(None) == ()
    assert normalize_characteristic_filters(["cc03", "", "bb02"]) == ("cc03", "bb02")


def test_matches_targets_searches_names_services_and_manufacturer_data() -> None:
    observation = ObservedDevice(
        address="FDEB1973-4261-02AF-B843-5027972A709B",
        name="ILCE-7CM2",
        local_name=None,
        rssi=-52,
        service_uuids=("00001800-0000-1000-8000-00805f9b34fb",),
        manufacturer_data={0x012D: b"\x03\x00e"},
    )

    assert matches_targets(observation, ("7CM2",))
    assert matches_targets(observation, ("012d",))
    assert not matches_targets(observation, ("STB-6252C",))


def test_matches_characteristic_filters_accepts_empty_or_uuid_substrings() -> None:
    uuid = "0000cc03-0000-1000-8000-00805f9b34fb"

    assert matches_characteristic_filters(uuid, ())
    assert matches_characteristic_filters(uuid, ("CC03",))
    assert not matches_characteristic_filters(uuid, ("bb02",))


def test_read_value_to_dict_renders_hex_payload() -> None:
    characteristic = CharacteristicInfo(
        uuid="0000cc06-0000-1000-8000-00805f9b34fb",
        handle=54,
        properties=("read",),
        description="Vendor specific",
        descriptors=(),
    )
    read_value = ReadValue(
        service_uuid="8000cc00-cc00-ffff-ffff-ffffffffffff",
        characteristic=characteristic,
        value=b"\x01\x02",
        error=None,
    )

    assert read_value.to_dict()["value_hex"] == "01 02"
    assert read_value.to_dict()["value_len"] == 2
    assert read_value.to_dict()["error"] is None


def test_notification_event_handles_characteristic_like_sender() -> None:
    class Sender:
        uuid = "0000cc03-0000-1000-8000-00805f9b34fb"
        handle = 51

    event = notification_event(Sender(), bytearray(b"\x0a\x0b"))

    assert event.uuid == "0000cc03-0000-1000-8000-00805f9b34fb"
    assert event.handle == 51
    assert event.data == b"\x0a\x0b"
    assert event.to_dict()["data_hex"] == "0a 0b"
