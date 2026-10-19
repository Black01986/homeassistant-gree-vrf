"""Cloud discovery: parent gateway / indoor unit mapping."""

from __future__ import annotations

from types import SimpleNamespace

from custom_components.gree_custom.aiogree.api import gree_discover_devices_cloud

from .conftest import CHILD_MAC, PARENT_MAC


def _cloud_device(mac: str, pmac: str, name: str) -> SimpleNamespace:
    return SimpleNamespace(
        mac=mac,
        pmac=pmac,
        name=name,
        key="synthetic-key",
        catalog="",
        brand="",
        prodModel="",
        subdivCode="",
        vender="",
        mid="",
        hid="",
        ver="",
    )


class _FakeCloud:
    user_id = 1234
    username = "user@example.com"

    def __init__(self, devices) -> None:
        self._devices = devices

    async def get_all_devices(self):
        return self._devices


async def test_child_is_routed_through_parent() -> None:
    cloud = _FakeCloud(
        [
            _cloud_device("c0ffee000001", "", "Bedroom"),
            _cloud_device(CHILD_MAC, PARENT_MAC, "Commercial ACf700"),
        ]
    )

    devices = {d.mac: d for d in await gree_discover_devices_cloud(cloud)}

    assert devices[CHILD_MAC].mac_controller_mqtt == PARENT_MAC
    assert devices[CHILD_MAC].mac_controller_local == ""
    assert devices["c0ffee000001"].mac_controller_mqtt == "c0ffee000001"


async def test_pmac_wins_over_mac_heuristic() -> None:
    """An indoor unit id that does not follow the `<parent>00` pattern still uses pmac."""
    cloud = _FakeCloud([_cloud_device("aabbccddeeff3a", "AA:BB:CC:DD:EE:FF", "Unit")])

    (device,) = await gree_discover_devices_cloud(cloud)

    assert device.mac_controller_mqtt == PARENT_MAC
