"""MQTT transport tests with a fake broker client."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json

import aiomqtt
import pytest

from custom_components.gree_custom.aiogree import transport_mqtt
from custom_components.gree_custom.aiogree.api import gree_get_status, gree_set_status
from custom_components.gree_custom.aiogree.cipher import EncryptionVersion, get_cipher
from custom_components.gree_custom.aiogree.helpers import gree_decrypt_pack, gree_encrypt_pack
from custom_components.gree_custom.aiogree.cloud_api import GreeRegion
from custom_components.gree_custom.aiogree.errors import GreeConnectionError
from custom_components.gree_custom.aiogree.transport_mqtt import GreeMqttTransport

from .conftest import CHILD_MAC, DEVICE_KEY, PARENT_MAC


@dataclass
class _Message:
    topic: str
    payload: bytes


class FakeMqttClient:
    instances: list[FakeMqttClient] = []

    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs
        self.subscribed: list[str] = []
        self.published: list[tuple[str, str]] = []
        self.queue: asyncio.Queue = asyncio.Queue()
        self.auto_reply = True
        FakeMqttClient.instances.append(self)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args) -> None:
        return None

    async def subscribe(self, topic: str, qos: int = 0) -> None:
        self.subscribed.append(topic)

    async def unsubscribe(self, topic: str) -> None:
        self.subscribed.remove(topic)

    async def publish(self, topic: str, payload: str, qos: int = 0) -> None:
        self.published.append((topic, payload))
        if self.auto_reply:
            mac = topic.split("/", 1)[1]
            self.queue.put_nowait(_Message(f"response/{mac}/x", f"echo:{payload}".encode()))

    @property
    def messages(self):
        return self._messages()

    async def _messages(self):
        while True:
            item = await self.queue.get()
            if isinstance(item, Exception):
                raise item
            yield item


@pytest.fixture
def fake_client(monkeypatch):
    FakeMqttClient.instances = []
    monkeypatch.setattr(transport_mqtt.aiomqtt, "Client", FakeMqttClient)
    return FakeMqttClient


def _transport(timeout: float = 1.0) -> GreeMqttTransport:
    return GreeMqttTransport("1234", "token", GreeRegion.EU, timeout=timeout)


async def test_request_goes_to_gateway_topic(fake_client) -> None:
    transport = _transport()
    await transport.subscribe(PARENT_MAC)

    response = await transport.request(PARENT_MAC, "hello")

    client = fake_client.instances[0]
    assert client.published == [(f"request/{PARENT_MAC}", "hello")]
    assert response == "echo:hello"
    assert f"status/{PARENT_MAC}/#" in client.subscribed

    await transport.unsubscribe(PARENT_MAC)
    assert not transport.connected


async def test_timeout_is_a_connection_error(fake_client) -> None:
    transport = _transport(timeout=0.05)
    await transport.subscribe(PARENT_MAC)
    fake_client.instances[0].auto_reply = False

    with pytest.raises(GreeConnectionError):
        await transport.request(PARENT_MAC, "hello")

    await transport.disconnect()


async def test_reconnects_and_resubscribes_after_connection_loss(fake_client) -> None:
    transport = _transport()
    await transport.subscribe(PARENT_MAC)
    first = fake_client.instances[0]

    first.queue.put_nowait(aiomqtt.MqttError("connection lost"))
    for _ in range(5):
        await asyncio.sleep(0)

    assert not transport.connected

    response = await transport.request(PARENT_MAC, "again")

    assert response == "echo:again"
    assert len(fake_client.instances) == 2
    second = fake_client.instances[1]
    assert f"status/{PARENT_MAC}/#" in second.subscribed
    assert f"response/{PARENT_MAC}/#" in second.subscribed

    await transport.disconnect()


async def test_pending_request_fails_fast_on_connection_loss(fake_client) -> None:
    transport = _transport(timeout=5)
    await transport.subscribe(PARENT_MAC)
    client = fake_client.instances[0]
    client.auto_reply = False

    task = asyncio.create_task(transport.request(PARENT_MAC, "hello"))
    await asyncio.sleep(0.01)
    client.queue.put_nowait(aiomqtt.MqttError("connection lost"))

    with pytest.raises(GreeConnectionError):
        await asyncio.wait_for(task, 1)

    await transport.disconnect()


async def test_unsubscribe_after_connection_loss(fake_client) -> None:
    transport = _transport()
    await transport.subscribe(PARENT_MAC)
    fake_client.instances[0].queue.put_nowait(aiomqtt.MqttError("connection lost"))
    for _ in range(5):
        await asyncio.sleep(0)

    await transport.unsubscribe(PARENT_MAC)

    assert not transport.connected_devices


async def test_skips_responses_rejected_by_accept(fake_client) -> None:
    transport = _transport()
    await transport.subscribe(PARENT_MAC)
    client = fake_client.instances[0]
    client.auto_reply = False

    task = asyncio.create_task(
        transport.request(PARENT_MAC, "q", accept=lambda raw: raw == "good")
    )
    await asyncio.sleep(0.01)
    client.queue.put_nowait(_Message(f"response/{PARENT_MAC}/x", b"stale"))
    client.queue.put_nowait(_Message(f"response/{PARENT_MAC}/x", b"good"))

    assert await asyncio.wait_for(task, 1) == "good"

    await transport.disconnect()


class DuplicatingGateway(FakeMqttClient):
    """Gateway that answers every request twice, the copy arriving late.

    The copy of an answer comes in after the next request was sent, ahead of
    that request's own answer.
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.cipher = get_cipher(EncryptionVersion.V1, DEVICE_KEY)
        self.state = {"Pow": 0, "Mod": 1, "SetTem": 25}
        self._late_copy: bytes | None = None

    async def publish(self, topic: str, payload: str, qos: int = 0) -> None:
        self.published.append((topic, payload))
        request = gree_decrypt_pack(json.loads(payload), self.cipher)
        pack = request["pack"]

        if pack["t"] == "cmd":
            self.state.update(dict(zip(pack["opt"], pack["p"], strict=True)))
            answer = {"t": "res", "r": 200, "sub": pack["sub"], "opt": pack["opt"], "p": pack["p"]}
        else:
            cols = [c for c in pack["cols"] if c in self.state]
            answer = {"t": "dat", "r": 200, "sub": pack["mac"], "cols": cols, "dat": [self.state[c] for c in cols]}

        raw = json.dumps(gree_encrypt_pack({"t": "pack", "pack": answer}, self.cipher)).encode()
        topic = f"response/{PARENT_MAC}/{CHILD_MAC}"
        if self._late_copy is not None:
            self.queue.put_nowait(_Message(topic, self._late_copy))
        self.queue.put_nowait(_Message(topic, raw))
        self._late_copy = raw


async def test_duplicate_command_answer_is_not_taken_as_status(monkeypatch) -> None:
    """Reproduces 'No data received while getting device status' after a command."""
    monkeypatch.setattr(transport_mqtt.aiomqtt, "Client", DuplicatingGateway)
    transport = _transport()
    await transport.subscribe(PARENT_MAC)
    cipher = get_cipher(EncryptionVersion.V1, DEVICE_KEY)

    await gree_set_status(PARENT_MAC, CHILD_MAC, 0, {"Pow": 1}, cipher, transport)
    res = await gree_get_status(PARENT_MAC, CHILD_MAC, 0, ["Pow", "Mod", "SetTem"], cipher, transport)

    assert res.prop_values == {"Pow": 1, "Mod": 1, "SetTem": 25}

    # And the duplicate of that status answer does not confuse the next command
    await gree_set_status(PARENT_MAC, CHILD_MAC, 0, {"SetTem": 22}, cipher, transport)
    res = await gree_get_status(PARENT_MAC, CHILD_MAC, 0, ["SetTem"], cipher, transport)
    assert res.prop_values == {"SetTem": 22}

    await transport.disconnect()
