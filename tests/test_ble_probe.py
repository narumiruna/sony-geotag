from sonygeotag.ble_probe import DEFAULT_TARGETS
from sonygeotag.ble_probe import ObservedDevice
from sonygeotag.ble_probe import bytes_to_hex
from sonygeotag.ble_probe import manufacturer_data_to_dict
from sonygeotag.ble_probe import matches_targets
from sonygeotag.ble_probe import normalize_targets


def test_bytes_to_hex_spaces_bytes() -> None:
    assert bytes_to_hex(b"\x03\x00e") == "03 00 65"


def test_manufacturer_data_to_dict_uses_hex_company_ids() -> None:
    assert manufacturer_data_to_dict({0x012D: b"\x03\x00"}) == {"0x012d": "03 00"}


def test_normalize_targets_defaults_to_a7c2_names() -> None:
    assert normalize_targets(None) == DEFAULT_TARGETS
    assert normalize_targets([]) == DEFAULT_TARGETS


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
