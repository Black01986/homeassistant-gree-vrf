"""Handles MQTT connections."""

import asyncio
from collections.abc import Callable
import contextlib
import json
import logging
import random
import ssl
from typing import override

import aiomqtt

from .cloud_api import GreeRegion
from .errors import GreeConnectionError, GreeRuntimeError
from .transport import GreeBaseTransport

_LOGGER = logging.getLogger(__name__)


MQTT_SERVERS = {
    GreeRegion.AU: "mqtt-au.gree.com",
    GreeRegion.CN: "mqtt-cn.gree.com",
    GreeRegion.AS: "mqtt-as.gree.com",
    GreeRegion.EU: "mqtt-eu.gree.com",
    GreeRegion.IN: "mqtt-in.gree.com",
    GreeRegion.LA: "mqtt-la.gree.com",
    GreeRegion.ME: "mqtt-me.gree.com",
    GreeRegion.US: "mqtt-us.gree.com",
    GreeRegion.RU: "mqtt-ru.gree.com",
    GreeRegion.SA: "mqtt-sa.gree.com",
}


def _create_tls_context() -> ssl.SSLContext:
    """Create the TLS context for the Gree broker."""
    tls_context = ssl.create_default_context()
    # Allow self-signed certificates (Gree broker uses custom cert)
    tls_context.check_hostname = False
    tls_context.verify_mode = ssl.CERT_NONE
    return tls_context


class GreeMqttTransport(GreeBaseTransport):
    """MQTT transport."""

    batch_support = True

    def __init__(
        self,
        user_id: str,
        token: str,
        region: GreeRegion,
        port: int = 1984,
        keepalive: int = 60,
        timeout: float = 10.0,
    ) -> None:
        """Initialize the MQTT transport object."""
        super().__init__()

        self._user_id = user_id
        self._token = token
        self._region = region
        self._port = port

        self._timeout = timeout
        self._keepalive = keepalive

        # Stable for the lifetime of this MQTT session.
        self._cid = str(random.randint(1_000_000_000, 9_999_999_999))

        self._client: aiomqtt.Client | None = None
        self._connected = False

        self._receive_task: asyncio.Task | None = None

        self._request_lock = asyncio.Lock()
        # Responses received while a request waits (str), or the error that
        # ended the connection
        self._pending: asyncio.Queue[str | Exception] | None = None

    @override
    def __str__(self) -> str:
        """Representation of the MQTT transport."""
        return f"MQTT({self._user_id}, {self._region.name})"

    @override
    async def connect(self) -> None:
        if self._connected:
            return

        # A client left behind by a lost connection must be closed first
        await self._discard_client()

        # Loading the CA store does disk I/O: keep it off the event loop
        tls_context = await asyncio.get_running_loop().run_in_executor(
            None, _create_tls_context
        )

        self._client = aiomqtt.Client(
            hostname=MQTT_SERVERS[self._region],
            port=self._port,
            username=self._user_id,
            password=self._token,
            identifier=f"app_{self._cid}",
            protocol=aiomqtt.ProtocolVersion.V311,
            keepalive=self._keepalive,
            tls_context=tls_context,
            timeout=self._timeout,
        )

        await self._client.__aenter__()  # pylint: disable=unnecessary-dunder-call
        self._connected = True

        # Create receiving task
        self._receive_task = asyncio.create_task(self._receive_loop())
        self._receive_task.add_done_callback(self._receive_task_done)

        _LOGGER.debug("Connected to MQTT broker %s:%d", self._region.value, self._port)

    @override
    async def disconnect(self) -> None:
        # Clear receive task
        if self._receive_task:
            self._receive_task.remove_done_callback(self._receive_task_done)
            self._receive_task.cancel()

            # A task that already ended re-raises its own error here
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._receive_task

            self._receive_task = None

        # Close MQTT client
        await self._discard_client()

        self._pending = None

        self._connected = False
        _LOGGER.debug("Disconnected from MQTT broker")

    @override
    async def subscribe(self, mac_controller: str) -> None:
        await self._ensure_connected()

        if not self._connected or not self._client:
            raise GreeRuntimeError("MQTT transport not connected")

        if mac_controller not in self.connected_devices:
            await self._subscribe_topics(mac_controller)

        self.connected_devices[mac_controller] += 1

    @staticmethod
    def _topics(mac_controller: str) -> list[str]:
        return [
            f"response/{mac_controller}/#",
            f"status/{mac_controller}/#",
            f"connect/{mac_controller}",
        ]

    async def _subscribe_topics(self, mac_controller: str) -> None:
        if not self._client:
            raise GreeRuntimeError("MQTT transport not connected")

        for topic in self._topics(mac_controller):
            await self._client.subscribe(topic, qos=1)
            _LOGGER.debug("Subscribed to topic: %s", topic)

    async def _ensure_connected(self) -> None:
        """Connect, and after a lost connection restore the subscriptions."""
        if self._connected:
            return

        reconnecting = bool(self.connected_devices)
        if reconnecting:
            _LOGGER.info("Reconnecting to MQTT broker %s", self._region.name)

        await self.connect()

        if reconnecting:
            for mac_controller in list(self.connected_devices):
                await self._subscribe_topics(mac_controller)

    @override
    async def unsubscribe(self, mac_controller: str) -> None:
        if self.connected_devices[mac_controller] > 1:
            self.connected_devices[mac_controller] -= 1
        else:
            self.connected_devices.pop(mac_controller, None)

            # After a lost connection the broker already dropped the subscriptions
            if self._connected and self._client:
                for topic in self._topics(mac_controller):
                    await self._client.unsubscribe(topic)
                    _LOGGER.debug("Unsubscribed from topic: %s", topic)

        if len(self.connected_devices) == 0:
            return await self.disconnect()

        return None

    @override
    async def request(
        self,
        mac_controller: str,
        json_str: str,
        accept: Callable[[str], bool] | None = None,
    ) -> str:
        """Publish one MQTT request and wait for its response."""

        try:
            await self._ensure_connected()
        except aiomqtt.MqttError as err:
            raise GreeConnectionError(
                f"Cannot connect to MQTT broker {self._region.name}"
            ) from err

        if not self._connected or not self._client:
            raise GreeConnectionError("MQTT transport not connected")

        async with self._request_lock:
            queue: asyncio.Queue[str | Exception] = asyncio.Queue()
            self._pending = queue
            loop = asyncio.get_running_loop()

            try:
                await self._client.publish(
                    topic=f"request/{mac_controller}",
                    payload=json_str,
                    qos=1,
                )

                deadline = loop.time() + self._timeout
                while True:
                    item = await asyncio.wait_for(
                        queue.get(), timeout=max(0.0, deadline - loop.time())
                    )
                    if isinstance(item, Exception):
                        raise item
                    if accept is None or accept(item):
                        return item

                    # Duplicate or late answer to an earlier request
                    _LOGGER.debug(
                        "Ignoring MQTT response from %s that does not answer the request",
                        mac_controller,
                    )

            except TimeoutError as err:
                raise GreeConnectionError(
                    f"No MQTT response from {mac_controller} within {self._timeout}s"
                ) from err

            except aiomqtt.MqttError as err:
                self._connected = False
                raise GreeConnectionError(
                    f"MQTT error while requesting {mac_controller}"
                ) from err

            finally:
                self._pending = None

    def _receive_task_done(self, task: asyncio.Task[None]) -> None:
        """Handle receive task completion."""
        if task.cancelled():
            _LOGGER.debug("MQTT receive loop cancelled")
            return

        exception = task.exception()
        if exception:
            _LOGGER.warning(
                "MQTT connection to %s lost (%s). It will reconnect on the next request",
                self._region.name,
                type(exception).__name__,
            )
            _LOGGER.debug("MQTT receive loop exception", exc_info=exception)
        else:
            _LOGGER.debug("MQTT receive loop stopped normally")

        # The receive loop only ends when the connection is gone
        self._connected = False
        if self._pending is not None:
            self._pending.put_nowait(GreeConnectionError("MQTT connection lost"))

    async def _discard_client(self) -> None:
        """Close a client whose connection was lost."""
        if self._client is None:
            return

        client, self._client = self._client, None
        with contextlib.suppress(Exception):
            await client.__aexit__(None, None, None)  # pylint: disable=unnecessary-dunder-call

    async def _receive_loop(self) -> None:
        """Receive MQTT messages."""

        if not self._connected or not self._client:
            raise GreeRuntimeError("Transport not connected.")

        async for message in self._client.messages:
            topic = str(message.topic)
            payload = message.payload.decode()

            _LOGGER.debug("Received MQTT Message with topic: %s", topic)

            #
            # response/ completes pending request
            #
            if topic.startswith("response/") and self._pending is not None:
                self._pending.put_nowait(payload)
                continue

            #
            # status/ and connect/
            #
            for target_mac, listeners in self._listeners.items():
                if target_mac not in topic:
                    continue

                for listener in listeners:
                    try:
                        listener(topic, json.loads(payload))
                    except Exception:
                        _LOGGER.exception("MQTT listener raised")

    @property
    def connected(self) -> bool:
        """True while the broker connection is up."""
        return self._connected

    @property
    def cid(self) -> str:
        """MQTT client identifier used in request envelopes."""
        return self._cid
