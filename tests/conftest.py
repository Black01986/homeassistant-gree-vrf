"""Shared fixtures for the aiogree tests.

All MAC addresses and keys here are synthetic.
"""

from __future__ import annotations

import copy
from typing import Any

import pytest

from custom_components.gree_custom.aiogree.errors import GreeConnectionError
from custom_components.gree_custom.aiogree.transport import GreeBaseTransport
from custom_components.gree_custom.aiogree.transport_mqtt import GreeMqttTransport

PARENT_MAC = "aabbccddeeff"
CHILD_MAC = "aabbccddeeff00"
OTHER_CHILD_MAC = "aabbccddeeff01"
DEVICE_KEY = "0123456789abcdef"


class CommercialUnitSimulator:
    """Simulates the observed behaviour of a commercial unit behind a cloud gateway.

    * ``values``: props with a value.
    * ``valueless``: props echoed in ``cols`` but without a value in ``dat``.
      Asking for one of them together with others gives ``len(cols) != len(dat)``.
    * Unknown props are left out of ``cols`` altogether.
    * ``lag_reads``: after a command, this many reads still return the old
      state, like a VRF gateway answering from its cache.
    """

    def __init__(
        self,
        values: dict[str, Any],
        valueless: set[str] | None = None,
        sub_in_response: bool = True,
        lag_reads: int = 0,
        max_cols: int | None = None,
        silent_above: int | None = None,
    ) -> None:
        self.values = dict(values)
        self.valueless = set(valueless or ())
        self.sub_in_response = sub_in_response
        self.lag_reads = lag_reads
        # Larger requests get an empty answer (cols=[], dat=[])
        self.max_cols = max_cols
        # Larger requests get no answer at all (timeout)
        self.silent_above = silent_above
        self._lagging_left = 0
        self._old_values: dict[str, Any] = {}
        self.status_requests: list[list[str]] = []
        self.commands: list[dict[str, Any]] = []
        self.fail_commands: Exception | None = None
        self.fail_status: Exception | None = None

    def handle(self, pack: dict[str, Any]) -> dict[str, Any]:
        if pack.get("t") == "status":
            return self._status(pack)
        if pack.get("t") == "cmd":
            return self._cmd(pack)
        raise AssertionError(f"Unexpected pack {pack}")

    def _status(self, pack: dict[str, Any]) -> dict[str, Any]:
        if self.fail_status:
            raise self.fail_status

        requested = list(pack["cols"])
        self.status_requests.append(requested)

        if self.silent_above is not None and len(requested) > self.silent_above:
            raise GreeConnectionError("No MQTT response within 10.0s")

        if self.max_cols is not None and len(requested) > self.max_cols:
            return {"t": "dat", "r": 200, "mac": pack["mac"], "cols": [], "dat": []}

        values = self.values
        if self._lagging_left > 0:
            self._lagging_left -= 1
            values = {**self.values, **self._old_values}

        cols = [p for p in requested if p in values or p in self.valueless]
        dat = [values[p] for p in cols if p not in self.valueless]

        response: dict[str, Any] = {"t": "dat", "r": 200, "cols": cols, "dat": dat}
        if self.sub_in_response:
            response["sub"] = pack["mac"]
        response["mac"] = pack["mac"]
        return response

    def _cmd(self, pack: dict[str, Any]) -> dict[str, Any]:
        self.commands.append(copy.deepcopy(pack))
        if self.fail_commands:
            raise self.fail_commands

        self._old_values = {k: self.values.get(k) for k in pack["opt"] if k in self.values}
        for prop, value in zip(pack["opt"], pack["p"], strict=True):
            if prop in self.values:
                self.values[prop] = value
        self._lagging_left = self.lag_reads
        return {"t": "res", "r": 200, "opt": pack["opt"], "p": pack["p"]}


class FakeTransport(GreeBaseTransport):
    """In-memory transport answering with decrypted packs."""

    batch_support = True

    def __init__(self, simulator: CommercialUnitSimulator) -> None:
        GreeBaseTransport.__init__(self)
        self.simulator = simulator
        self.requests: list[tuple[str, dict[str, Any]]] = []
        self.offline = False
        self._connected = True

    def __str__(self) -> str:
        return "FakeTransport"

    @property
    def connected(self) -> bool:
        return self._connected

    async def connect(self) -> None:
        return None

    async def disconnect(self) -> None:
        return None

    async def subscribe(self, mac_controller: str) -> None:
        self.connected_devices[mac_controller] += 1

    async def unsubscribe(self, mac_controller: str) -> None:
        self.connected_devices[mac_controller] -= 1

    async def request(self, mac_controller: str, json_str: str) -> str:
        raise NotImplementedError

    async def request_json(self, mac_controller, payload, cipher):  # noqa: D102
        if self.offline:
            self._connected = False
            raise GreeConnectionError("offline")
        self._connected = True
        self.requests.append((mac_controller, copy.deepcopy(payload)))
        return {"pack": self.simulator.handle(payload["pack"])}

    def push(self, topic: str, payload: dict[str, Any]) -> None:
        """Deliver an unsolicited message the way a transport receive loop does."""
        for target_mac, listeners in list(self._listeners.items()):
            if target_mac not in topic:
                continue
            for listener in list(listeners):
                listener(topic, copy.deepcopy(payload))


class FakeMqttTransport(FakeTransport, GreeMqttTransport):
    """Fake transport that is recognised as the MQTT transport."""

    def __init__(self, simulator: CommercialUnitSimulator) -> None:
        FakeTransport.__init__(self, simulator)


COMMERCIAL_STATUS = {
    "Pow": 1,
    "Mod": 1,
    "SetTem": 24,
    "WdSpd": 3,
    "SwUpDn": 0,
    "SwingLfRig": 0,
    "TemUn": 0,
    "InTem": 65,
    "InTemEn": 1,
    "Add0.5": 0,
    "FaultDisplay": 0,
}

# Device info: 10 of the 14 props are echoed back, and one of those has no value
COMMERCIAL_INFO = {
    "mac": PARENT_MAC,
    "name": "Commercial AC",
    "ModelType": "SQ-240",
    "mid": "60",
    "MidType": "1",
    "hid": "362001000967V2.0.0.bin",
    "vender": "1",
    "ver": "V2.0.0",
    "busVol": 0,
}
COMMERCIAL_VALUELESS = {"bc"}


@pytest.fixture
def commercial_unit() -> CommercialUnitSimulator:
    return CommercialUnitSimulator(
        values={**COMMERCIAL_STATUS, **COMMERCIAL_INFO},
        valueless=COMMERCIAL_VALUELESS,
    )
