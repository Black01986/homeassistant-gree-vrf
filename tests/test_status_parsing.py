"""Parser and status request tests, including the commercial unit `cols=10 dat=9` case."""

from __future__ import annotations

import pytest

from custom_components.gree_custom.aiogree.api import (
    InfoProp,
    _create_set_status_pack,
    gree_get_status,
    gree_process_status_pack,
    gree_set_status,
)
from custom_components.gree_custom.aiogree.cipher import EncryptionVersion, get_cipher
from custom_components.gree_custom.aiogree.errors import (
    GreeConnectionError,
    GreeMalformedResponseError,
    GreeProtocolError,
)

from .conftest import (
    CHILD_MAC,
    COMMERCIAL_INFO,
    DEVICE_KEY,
    PARENT_MAC,
    CommercialUnitSimulator,
    FakeTransport,
)

INFO_PROPS = [p.value for p in InfoProp]

# Shape of the response seen in the field (issue #1): 10 columns, 9 values
OBSERVED_INFO_RESPONSE = {
    "t": "dat",
    "r": 200,
    "mac": CHILD_MAC,
    "cols": ["mac", "name", "bc", "ModelType", "mid", "MidType", "hid", "vender", "ver", "busVol"],
    "dat": [PARENT_MAC, "Commercial AC", "SQ-240", "60", "1", "fw.bin", "1", "V2.0.0", 0],
}


@pytest.fixture
def cipher():
    return get_cipher(EncryptionVersion.V1, DEVICE_KEY)


def test_malformed_response_is_not_zipped() -> None:
    """cols=10 dat=9 cannot be mapped: raise, never assign shifted values."""
    with pytest.raises(GreeMalformedResponseError, match="cols=10 dat=9"):
        gree_process_status_pack(OBSERVED_INFO_RESPONSE, INFO_PROPS)


def test_malformed_response_is_a_protocol_error() -> None:
    """Existing callers that catch GreeProtocolError keep working."""
    with pytest.raises(GreeProtocolError):
        gree_process_status_pack(OBSERVED_INFO_RESPONSE, INFO_PROPS)


def test_well_formed_response() -> None:
    res = gree_process_status_pack(
        {"cols": ["Pow", "Mod", "SetTem"], "dat": [1, 4, 22]}, ["Pow", "Mod", "SetTem", "Tur"]
    )
    assert res.prop_values == {"Pow": 1, "Mod": 4, "SetTem": 22}
    assert res.missing_props == ["Tur"]


def test_single_prop_without_value_is_missing() -> None:
    res = gree_process_status_pack({"cols": ["bc"], "dat": []}, ["bc"])
    assert res.prop_values == {}
    assert res.missing_props == ["bc"]


def test_empty_response_marks_all_missing() -> None:
    res = gree_process_status_pack({"cols": [], "dat": []}, ["host"])
    assert res.prop_values == {}
    assert res.missing_props == ["host"]


def test_missing_arrays_is_an_error() -> None:
    with pytest.raises(GreeProtocolError):
        gree_process_status_pack({"t": "dat", "r": 200}, ["Pow"])


def test_response_for_another_request_is_rejected() -> None:
    """A late answer to an earlier request must not be taken for this one."""
    with pytest.raises(GreeMalformedResponseError):
        gree_process_status_pack({"cols": ["mac", "name"], "dat": ["x", "y"]}, ["Pow", "Mod"])


def test_vrf_indoor_temperature_normalization() -> None:
    res = gree_process_status_pack(
        {"sub": CHILD_MAC, "cols": ["InTemEn", "InTem"], "dat": [1, 65]},
        ["InTemEn", "InTem"],
    )
    assert res.prop_values["TemSen"] == 65


def test_vrf_indoor_temperature_not_used_when_disabled() -> None:
    res = gree_process_status_pack(
        {"sub": CHILD_MAC, "cols": ["InTemEn", "InTem"], "dat": [0, 65]},
        ["InTemEn", "InTem"],
    )
    assert "TemSen" not in res.prop_values


def test_partial_vrf_pack_does_not_invent_temrec() -> None:
    """A pushed pack with only power must not reset the setpoint half degree bit."""
    res = gree_process_status_pack({"sub": CHILD_MAC, "cols": ["Pow"], "dat": [0]}, None)
    assert res.prop_values == {"Pow": 0}


def test_vrf_temrec_derived_with_setpoint() -> None:
    res = gree_process_status_pack(
        {"sub": CHILD_MAC, "cols": ["SetTem"], "dat": [24]}, ["SetTem", "TemRec"]
    )
    assert res.prop_values == {"SetTem": 24, "TemRec": 0}
    assert res.missing_props == []


async def test_status_falls_back_to_single_prop_queries(cipher) -> None:
    """The info batch is re-queried prop by prop and every value lands on its own prop."""
    sim = CommercialUnitSimulator(values=COMMERCIAL_INFO, valueless={"bc"})
    transport = FakeTransport(sim)

    res = await gree_get_status(PARENT_MAC, CHILD_MAC, 0, INFO_PROPS, cipher, transport)

    # First the full batch, then smaller ones
    assert sim.status_requests[0] == INFO_PROPS
    assert len(sim.status_requests) > 1

    assert res.prop_values == COMMERCIAL_INFO
    assert sorted(res.missing_props) == sorted(
        ["bc", "ModelNew", "host", "wifiStatus", "wifiReset"]
    )
    assert "bc" not in res.prop_values


async def test_empty_answer_to_large_request_is_split(cipher) -> None:
    """A unit that answers nothing to big requests still gets read, not marked unsupported."""
    values = {"Pow": 1, "Mod": 1, "SetTem": 24, "WdSpd": 3, "SwUpDn": 0, "TemUn": 0}
    sim = CommercialUnitSimulator(values=values, max_cols=2)
    transport = FakeTransport(sim)
    props = [*values, "Tur", "Quiet"]

    res = await gree_get_status(PARENT_MAC, CHILD_MAC, 0, props, cipher, transport)

    assert res.prop_values == values
    assert sorted(res.missing_props) == ["Quiet", "Tur"]


async def test_status_without_mismatch_uses_one_request(cipher) -> None:
    sim = CommercialUnitSimulator(values={"Pow": 1, "Mod": 2})
    transport = FakeTransport(sim)

    res = await gree_get_status(PARENT_MAC, CHILD_MAC, 0, ["Pow", "Mod"], cipher, transport)

    assert len(sim.status_requests) == 1
    assert res.prop_values == {"Pow": 1, "Mod": 2}


async def test_status_request_routing(cipher) -> None:
    """Requests go to the gateway topic and name the indoor unit in the pack."""
    sim = CommercialUnitSimulator(values={"Pow": 1})
    transport = FakeTransport(sim)

    await gree_get_status(PARENT_MAC, CHILD_MAC, 0, ["Pow"], cipher, transport)

    controller, payload = transport.requests[0]
    assert controller == PARENT_MAC
    assert payload["pack"] == {"t": "status", "mac": CHILD_MAC, "cols": ["Pow"]}


async def test_connection_error_during_fallback_propagates(cipher) -> None:
    sim = CommercialUnitSimulator(values=COMMERCIAL_INFO, valueless={"bc"})
    transport = FakeTransport(sim)

    original = sim._status
    calls = 0

    def flaky(pack):
        nonlocal calls
        calls += 1
        if calls == 3:
            transport._connected = False  # what a lost MQTT connection reports
            raise GreeConnectionError("lost")
        return original(pack)

    sim._status = flaky

    with pytest.raises(GreeConnectionError):
        await gree_get_status(PARENT_MAC, CHILD_MAC, 0, INFO_PROPS, cipher, transport)


def test_set_pack_targets_indoor_unit() -> None:
    pack = _create_set_status_pack(CHILD_MAC, {"Pow": 1, "SetTem": 23, "Mod": 1})
    assert pack["sub"] == CHILD_MAC
    assert pack["t"] == "cmd"
    # Mode first, temperature next, power last
    assert pack["opt"] == ["Mod", "SetTem", "Pow"]
    assert pack["p"] == [1, 23, 1]


async def test_set_status_error_code_raises(cipher) -> None:
    sim = CommercialUnitSimulator(values={"Pow": 0})
    sim._cmd = lambda pack: {"t": "res", "r": 400}
    transport = FakeTransport(sim)

    with pytest.raises(GreeProtocolError):
        await gree_set_status(PARENT_MAC, CHILD_MAC, 0, {"Pow": 1}, cipher, transport)


async def test_set_status_tolerates_mismatched_echo(cipher) -> None:
    """An applied command must not fail because the echo lists do not line up."""
    sim = CommercialUnitSimulator(values={"Pow": 0, "Mod": 1})
    sim._cmd = lambda pack: {"t": "res", "r": 200, "opt": ["Pow", "Mod"], "p": [1]}
    transport = FakeTransport(sim)

    result = await gree_set_status(PARENT_MAC, CHILD_MAC, 0, {"Pow": 1, "Mod": 1}, cipher, transport)

    assert result == {"Pow": 1, "Mod": 1}


async def test_set_status_tolerates_non_numeric_echo(cipher) -> None:
    sim = CommercialUnitSimulator(values={"Pow": 0})
    sim._cmd = lambda pack: {"t": "res", "r": 200, "opt": ["Pow"], "p": [""]}
    transport = FakeTransport(sim)

    await gree_set_status(PARENT_MAC, CHILD_MAC, 0, {"Pow": 1}, cipher, transport)
