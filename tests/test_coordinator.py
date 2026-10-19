"""Coordinator tests: follow-up polls, pushed updates and availability."""

from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.gree_custom.aiogree.cipher import EncryptionVersion, get_cipher
from custom_components.gree_custom.aiogree.helpers import gree_encrypt_pack
from custom_components.gree_custom.const import DOMAIN
from custom_components.gree_custom.coordinator import (
    FOLLOW_UP_REFRESH_DELAY,
    FOLLOW_UP_REFRESH_MAX,
    GreeCoordinator,
)

from .conftest import CHILD_MAC, DEVICE_KEY, PARENT_MAC, CommercialUnitSimulator
from .test_device import _bind


async def _coordinator(hass: HomeAssistant, sim: CommercialUnitSimulator):
    device, transport = await _bind(sim)
    entry = MockConfigEntry(domain=DOMAIN, data={})
    entry.add_to_hass(hass)
    coordinator = GreeCoordinator(
        hass,
        entry,
        scan_interval=60,
        check_availability=True,
        restore_states=False,
        device_config={},
        device=device,
    )
    await coordinator._setup()
    await coordinator.async_refresh()
    return coordinator, device, transport


async def _advance(hass: HomeAssistant, seconds: float) -> None:
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=seconds))
    await hass.async_block_till_done()


async def test_follow_up_poll_picks_up_lagging_gateway(
    hass: HomeAssistant, commercial_unit
) -> None:
    commercial_unit.lag_reads = 1
    coordinator, device, _ = await _coordinator(hass, commercial_unit)

    device.set_power_mode(False)
    await coordinator.push_device_status()
    assert device.power_mode is True  # gateway still answers from its cache

    await _advance(hass, FOLLOW_UP_REFRESH_DELAY + 0.5)

    assert device.power_mode is False
    assert not device.unconfirmed_props
    assert coordinator._unsub_follow_up is None

    await coordinator.async_shutdown()


async def test_follow_up_polls_stop_on_rejected_command(
    hass: HomeAssistant, commercial_unit, caplog
) -> None:
    coordinator, device, _ = await _coordinator(hass, commercial_unit)
    commercial_unit._cmd = lambda pack: {"t": "res", "r": 200}

    device.set_target_temperature(18)
    await coordinator.push_device_status()
    reads_before = len(commercial_unit.status_requests)

    with caplog.at_level(logging.WARNING):
        for _ in range(FOLLOW_UP_REFRESH_MAX + 2):
            await _advance(hass, FOLLOW_UP_REFRESH_DELAY + 0.5)

    polls = [r for r in commercial_unit.status_requests[reads_before:] if "Pow" in r]
    assert len(polls) == FOLLOW_UP_REFRESH_MAX
    assert device.target_temperature == 24.0
    assert "did not report the sent values" in caplog.text

    await coordinator.async_shutdown()


async def test_pushed_status_reaches_entities(hass: HomeAssistant, commercial_unit) -> None:
    coordinator, device, transport = await _coordinator(hass, commercial_unit)
    updates: list[bool] = []
    coordinator.async_add_listener(lambda: updates.append(device.power_mode))

    pack = {"t": "dat", "sub": CHILD_MAC, "cols": ["Pow"], "dat": [0]}
    transport.push(
        f"status/{PARENT_MAC}/{CHILD_MAC}",
        gree_encrypt_pack({"pack": pack}, get_cipher(EncryptionVersion.V1, DEVICE_KEY)),
    )

    assert updates == [False]

    await coordinator.async_shutdown()


async def test_failed_poll_is_not_presented_as_current(
    hass: HomeAssistant, commercial_unit
) -> None:
    coordinator, device, transport = await _coordinator(hass, commercial_unit)
    assert coordinator.last_update_success

    transport.offline = True
    await coordinator.async_refresh()
    assert not coordinator.last_update_success
    assert not device.available

    transport.offline = False
    await coordinator.async_refresh()
    assert coordinator.last_update_success
    assert device.available

    await coordinator.async_shutdown()


async def test_diagnostics_do_not_expose_secrets(hass: HomeAssistant, commercial_unit) -> None:
    from homeassistant.components.diagnostics import async_redact_data

    from custom_components.gree_custom.const import REDACT_KEYS

    coordinator, _, _ = await _coordinator(hass, commercial_unit)

    data = async_redact_data(coordinator.get_coordinator_diagnostics(), REDACT_KEYS)

    assert DEVICE_KEY not in repr(data)
    assert "polled_properties" in data

    await coordinator.async_shutdown()
