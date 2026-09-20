import asyncio
from typing import cast

import pytest
from bleak import BleakClient
from bleak.backends.characteristic import BleakGATTCharacteristic
from bleak.exc import BleakError

from sonygeotag.ble_probe import DEFAULT_TARGETS
from sonygeotag.ble_probe import CharacteristicInfo
from sonygeotag.ble_probe import ObservedDevice
from sonygeotag.ble_probe import ReadValue
from sonygeotag.ble_probe import _read_characteristic
from sonygeotag.ble_probe import bytes_to_hex
from sonygeotag.ble_probe import manufacturer_data_to_dict
from sonygeotag.ble_probe import matches_characteristic_filters
from sonygeotag.ble_probe import matches_targets
from sonygeotag.ble_probe import normalize_characteristic_filters
from sonygeotag.ble_probe import normalize_targets
from sonygeotag.ble_probe import notification_event
from sonygeotag.ble_probe import read_characteristic


def test_original_bounded_read_entry_point_remains_an_alias() -> None:
    assert _read_characteristic is read_characteristic


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


def test_stalled_gatt_dump_read_is_bounded() -> None:
    class HangingClient:
        async def read_gatt_char(self, _characteristic: object) -> bytes:
            await asyncio.sleep(3600)
            return b""

    value, error = asyncio.run(
        _read_characteristic(
            client=cast("BleakClient", HangingClient()),
            characteristic=cast("BleakGATTCharacteristic", object()),
            operation_timeout=0.01,
        )
    )

    assert value is None
    assert error == "TimeoutError: "


def test_notification_event_handles_characteristic_like_sender() -> None:
    class Sender:
        uuid = "0000cc03-0000-1000-8000-00805f9b34fb"
        handle = 51

    event = notification_event(Sender(), bytearray(b"\x0a\x0b"))

    assert event.uuid == "0000cc03-0000-1000-8000-00805f9b34fb"
    assert event.handle == 51
    assert event.data == b"\x0a\x0b"
    assert event.to_dict()["data_hex"] == "0a 0b"


@pytest.mark.parametrize("payload", [b"", b"\x01\x02", bytearray(b"\x01\x02")])
def test_bounded_read_returns_immutable_bytes_and_passes_characteristic(payload) -> None:
    characteristic = object()
    calls = []

    class Client:
        async def read_gatt_char(self, characteristic):
            calls.append(characteristic)
            return payload

    value, error = asyncio.run(_read_characteristic(Client(), characteristic, operation_timeout=1))

    assert type(value) is bytes
    assert value == bytes(payload)
    assert error is None
    assert calls == [characteristic]


@pytest.mark.parametrize(
    ("failure", "expected"),
    [
        (BleakError("radio unavailable"), "BleakError: radio unavailable"),
        (OSError("PRIVATE-ID"), "OSError: PRIVATE-ID"),
        (TimeoutError("read stalled"), "TimeoutError: read stalled"),
    ],
)
def test_bounded_read_retains_raw_diagnostic_errors(failure, expected) -> None:
    class Client:
        async def read_gatt_char(self, _characteristic):
            raise failure

    assert asyncio.run(_read_characteristic(Client(), object(), operation_timeout=1)) == (None, expected)


@pytest.mark.parametrize("failure", [RuntimeError("unexpected"), asyncio.CancelledError()])
def test_bounded_read_does_not_swallow_unexpected_errors_or_cancellation(failure) -> None:
    class Client:
        async def read_gatt_char(self, _characteristic):
            raise failure

    with pytest.raises(type(failure)) as raised:
        asyncio.run(_read_characteristic(Client(), object(), operation_timeout=1))
    assert raised.value is failure


def test_cancelling_bounded_read_cancels_the_underlying_operation() -> None:
    async def run():
        started = asyncio.Event()
        stopped = asyncio.Event()

        class Client:
            async def read_gatt_char(self, _characteristic):
                started.set()
                try:
                    await asyncio.Future()
                finally:
                    stopped.set()

        task = asyncio.create_task(_read_characteristic(Client(), object(), operation_timeout=60))
        await asyncio.wait_for(started.wait(), timeout=1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert stopped.is_set()

    asyncio.run(run())
