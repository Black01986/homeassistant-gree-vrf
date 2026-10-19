"""GreeDevice tests against a simulated commercial unit behind a cloud gateway."""

from __future__ import annotations

from typing import Any

import pytest

from custom_components.gree_custom.aiogree.api import GreeProp, OperationMode
from custom_components.gree_custom.aiogree.cipher import EncryptionVersion, get_cipher
from custom_components.gree_custom.aiogree.device import GreeDevice
from custom_components.gree_custom.aiogree.errors import (
    GreeConnectionError,
    GreeProtocolError,
)
from custom_components.gree_custom.aiogree.helpers import gree_encrypt_pack

from .conftest import (
    CHILD_MAC,
    COMMERCIAL_STATUS,
    DEVICE_KEY,
    OTHER_CHILD_MAC,
    PARENT_MAC,
    CommercialUnitSimulator,
    FakeMqttTransport,
)

STATUS_TOPIC = f"status/{PARENT_MAC}/{CHILD_MAC}"


async def _bind(sim: CommercialUnitSimulator, mac: str = CHILD_MAC, controller: str = PARENT_MAC):
    transport = FakeMqttTransport(sim)
    device = GreeDevice(name="Commercial AC", mac_addr=mac, preferred_encryption_key=DEVICE_KEY)
    await device.bind_with_transport(
        local_controller_mac="",
        local_transport=None,
        mqtt_controller_mac=controller,
        mqtt_transport=transport,
    )
    return device, transport


def _encrypted(pack: dict[str, Any]) -> dict[str, Any]:
    return gree_encrypt_pack({"t": "pack", "pack": pack}, get_cipher(EncryptionVersion.V1, DEVICE_KEY))


async def test_commercial_unit_binds_and_reads_real_state(commercial_unit) -> None:
    device, _ = await _bind(commercial_unit)

    assert device.available
    assert device.mac_address == CHILD_MAC
    assert device.mac_address_controller == PARENT_MAC

    assert device.power_mode is True
    assert device.operation_mode == OperationMode.cool
    assert device.target_temperature == 24.0
    # InTem is °C + 40
    assert device.indoors_temperature_c == 25

    # Device info that was answered is kept, the rest is simply absent
    assert device.device_model_id == "SQ-240 (1)"


async def test_unsupported_props_are_not_exposed(commercial_unit) -> None:
    device, _ = await _bind(commercial_unit)

    assert device.supports_property(GreeProp.POWER)
    assert device.supports_property(GreeProp.SWING_VERTICAL)
    # Not reported by the unit: must not be offered
    assert not device.supports_property(GreeProp.FEAT_TURBO_MODE)
    assert not device.supports_property(GreeProp.FEAT_HEALTH)
    assert device.humidity is None
    assert device.outdoors_temperature_c is None


async def test_unanswered_device_info_does_not_block_setup(commercial_unit) -> None:
    original = commercial_unit._status

    def no_info(pack):
        if "hid" in pack["cols"]:
            return {"t": "dat", "r": 200}
        return original(pack)

    commercial_unit._status = no_info

    device, _ = await _bind(commercial_unit)

    assert device.available
    assert device.power_mode is True
    assert device.firmware_version is None


async def test_commercial_unit_with_request_size_limit_binds(commercial_unit) -> None:
    commercial_unit.max_cols = 8
    device, _ = await _bind(commercial_unit)

    assert device.power_mode is True
    assert device.target_temperature == 24.0
    assert device.indoors_temperature_c == 25


async def test_unit_ignoring_large_requests_binds_and_remembers_limit(commercial_unit) -> None:
    """The gateway does not answer requests for many props: split, then keep the size."""
    commercial_unit.silent_above = 12
    device, transport = await _bind(commercial_unit)

    assert device.power_mode is True
    assert device.target_temperature == 24.0
    limit = transport.status_batch_limits[PARENT_MAC]
    assert limit <= 12

    commercial_unit.status_requests.clear()
    commercial_unit.values["Pow"] = 0
    await device.fetch_device_status()

    assert device.power_mode is False
    assert all(len(r) <= limit for r in commercial_unit.status_requests)


async def test_outage_is_not_mistaken_for_large_request(commercial_unit) -> None:
    device, transport = await _bind(commercial_unit)
    transport.offline = True

    with pytest.raises(GreeConnectionError):
        await device.fetch_device_status()

    assert not transport.status_batch_limits


async def test_numeric_strings_are_accepted() -> None:
    values = {k: str(v) for k, v in COMMERCIAL_STATUS.items()}
    values["SetTem"] = "24.0"
    sim = CommercialUnitSimulator(values=values)
    device, _ = await _bind(sim)

    assert device.power_mode is True
    assert device.target_temperature == 24.0


async def test_no_status_at_all_fails_setup() -> None:
    sim = CommercialUnitSimulator(values={})

    with pytest.raises(GreeProtocolError):
        await _bind(sim)


async def test_command_is_addressed_to_indoor_unit_without_beeper(commercial_unit) -> None:
    device, transport = await _bind(commercial_unit)

    device.set_power_mode(False)
    await device.push_device_status()

    assert commercial_unit.commands == [{"t": "cmd", "sub": CHILD_MAC, "opt": ["Pow"], "p": [0]}]
    controller, _ = transport.requests[-2]
    assert controller == PARENT_MAC
    # State comes from the read back, not from what was sent
    assert device.power_mode is False
    assert not device.unconfirmed_props


async def test_setpoint_and_mode_command(commercial_unit) -> None:
    device, _ = await _bind(commercial_unit)

    device.set_operation_mode(OperationMode.heat)
    device.set_target_temperature(21)
    await device.push_device_status()

    cmd = commercial_unit.commands[-1]
    assert cmd["opt"][0] == "Mod"
    assert dict(zip(cmd["opt"], cmd["p"], strict=True))["SetTem"] == 21
    assert device.operation_mode == OperationMode.heat
    assert device.target_temperature == 21.0


async def test_lagging_gateway_shows_reported_state(commercial_unit) -> None:
    """When the gateway answers from its cache, the old state is shown until it changes."""
    commercial_unit.lag_reads = 1
    device, _ = await _bind(commercial_unit)

    device.set_power_mode(False)
    await device.push_device_status()

    assert device.power_mode is True  # what the device reports, not what was sent
    assert device.unconfirmed_props == {GreeProp.POWER: 0}

    await device.fetch_device_status()

    assert device.power_mode is False
    assert not device.unconfirmed_props


async def test_failed_command_does_not_leave_assumed_state(commercial_unit) -> None:
    device, _ = await _bind(commercial_unit)
    commercial_unit.fail_commands = GreeConnectionError("timeout")

    device.set_power_mode(False)
    with pytest.raises(GreeConnectionError):
        await device.push_device_status()

    assert device.power_mode is True
    assert not device.available


async def test_rejected_command_shows_reported_state(commercial_unit) -> None:
    """The unit acknowledges but does not apply: HA must keep the real state."""
    device, _ = await _bind(commercial_unit)
    commercial_unit._cmd = lambda pack: {"t": "res", "r": 200, "opt": pack["opt"], "p": pack["p"]}

    device.set_target_temperature(18)
    await device.push_device_status()

    assert device.target_temperature == 24.0
    assert GreeProp.TARGET_TEMPERATURE in device.unconfirmed_props


async def test_pushed_status_updates_state(commercial_unit) -> None:
    """Changes made in the app or on the wall controller arrive without polling."""
    device, transport = await _bind(commercial_unit)

    transport.push(
        STATUS_TOPIC,
        _encrypted({"t": "dat", "sub": CHILD_MAC, "cols": ["Pow", "SetTem"], "dat": [0, 27]}),
    )

    assert device.power_mode is False
    assert device.target_temperature == 27.0


async def test_pushed_status_for_other_unit_is_ignored(commercial_unit) -> None:
    device, transport = await _bind(commercial_unit)

    transport.push(
        STATUS_TOPIC,
        _encrypted({"t": "dat", "sub": OTHER_CHILD_MAC, "cols": ["Pow"], "dat": [0]}),
    )
    # The gateway itself, without a unit id, is not this indoor unit either
    transport.push(
        STATUS_TOPIC,
        _encrypted({"t": "dat", "mac": PARENT_MAC, "cols": ["Pow"], "dat": [0]}),
    )

    assert device.power_mode is True


async def test_malformed_pushed_status_is_ignored(commercial_unit) -> None:
    device, transport = await _bind(commercial_unit)

    transport.push(
        STATUS_TOPIC,
        _encrypted({"t": "dat", "sub": CHILD_MAC, "cols": ["Pow", "SetTem"], "dat": [0]}),
    )

    assert device.power_mode is True
    assert device.target_temperature == 24.0


async def test_pushed_status_confirms_pending_command(commercial_unit) -> None:
    commercial_unit.lag_reads = 1
    device, transport = await _bind(commercial_unit)

    device.set_power_mode(False)
    await device.push_device_status()
    assert device.unconfirmed_props

    transport.push(
        STATUS_TOPIC,
        _encrypted({"t": "dat", "sub": CHILD_MAC, "cols": ["Pow"], "dat": [0]}),
    )

    assert not device.unconfirmed_props


async def test_poll_failure_marks_unavailable_and_recovers(commercial_unit) -> None:
    device, transport = await _bind(commercial_unit)

    transport.offline = True
    with pytest.raises(GreeConnectionError):
        await device.fetch_device_status()
    assert not device.available

    transport.offline = False
    commercial_unit.values["Pow"] = 0
    await device.fetch_device_status()
    assert device.available
    assert device.power_mode is False


async def test_residential_unit_keeps_beeper_and_push_handling() -> None:
    """Standalone units (controller == unit) are unchanged."""
    mac = "112233445566"
    sim = CommercialUnitSimulator(values=dict(COMMERCIAL_STATUS), sub_in_response=False)
    device, transport = await _bind(sim, mac=mac, controller=mac)

    device.set_power_mode(False)
    await device.push_device_status()

    cmd = sim.commands[-1]
    assert cmd["sub"] == mac
    assert "Buzzer_ON_OFF" in cmd["opt"]
    assert "BuzzerCtrl" in cmd["opt"]

    transport.push(
        f"status/{mac}",
        _encrypted({"t": "dat", "cols": ["Pow"], "dat": [1]}),
    )
    assert device.power_mode is True


async def test_half_degree_setpoint_is_shown(commercial_unit) -> None:
    """GMV units report half degrees as SetDeciTem (245 = 24.5)."""
    commercial_unit.values["SetDeciTem"] = 245
    device, _ = await _bind(commercial_unit)

    assert device.target_temperature == 24.5


async def test_half_degree_ignored_when_it_disagrees(commercial_unit) -> None:
    commercial_unit.values["SetDeciTem"] = 245
    device, _ = await _bind(commercial_unit)

    # A new setpoint waiting to be sent must not show the old half degree
    device.set_target_temperature(26)
    assert device.target_temperature == 26.0

    # Only whole degrees are sent, SetDeciTem is read only
    await device.push_device_status()
    assert "SetDeciTem" not in commercial_unit.commands[-1]["opt"]
