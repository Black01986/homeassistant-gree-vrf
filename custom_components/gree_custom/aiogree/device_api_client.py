"""Client used to interface with the actual device through a Transport."""

from collections.abc import Callable, Mapping
import logging

from .api import (
    BindingInfo,
    GreeProp,
    InfoProp,
    OtherProps,
    describe_status_pack,
    gree_get_status,
    gree_process_status_pack,
    gree_set_status,
    gree_try_bind,
)
from .cipher import CipherBase, EncryptionVersion, get_cipher
from .errors import (
    GreeBindingError,
    GreeConnectionError,
    GreeError,
    GreeProtocolError,
    GreeRuntimeError,
)
from .helpers import chunked, gree_decrypt_pack, redact_str
from .transport import GreeBaseTransport

_LOGGER = logging.getLogger(__name__)


class DeviceApiClient:
    """Manager for the communication with the device API."""

    def __init__(
        self,
        mac: str,
        userid: int,
    ) -> None:
        """Initialize the client."""
        self.controller_mac = ""
        self._mac = mac
        self._userid = userid

        self._transport: GreeBaseTransport | None = None

        self._cipher: CipherBase | None = None
        self._binding: BindingInfo | None = None

        self._bound = False
        self._available = False

        self._listeners: list[Callable[[dict[str, str]], None]] = []

    #
    # Binding
    #

    async def bind(
        self,
        controller_mac: str,
        preferred_version: EncryptionVersion | None = None,
        preferred_key: str | None = None,
        use_preferred_key_directly: bool = False,
    ) -> None:
        """Bind to the current transport using the suggested version and key."""
        if self._bound:
            return

        if self._transport is None:
            raise GreeBindingError("No transport configured")

        if not controller_mac or not bool(controller_mac.strip()):
            raise GreeBindingError("No controller MAC provided")

        self.controller_mac = controller_mac

        _LOGGER.info(
            "[%s:%s] Starting binding procedure", self.controller_mac, self._transport
        )

        await self._transport.subscribe(self.controller_mac)

        try:
            # Cloud discovery already returns the real per-device encryption key.
            # MQTT/VRF controllers do not necessarily implement the legacy bind
            # exchange, so use the supplied key directly when available.
            if use_preferred_key_directly and preferred_key and preferred_version is not None:
                result = BindingInfo(
                    encryption_key=preferred_key,
                    encryption_version=preferred_version,
                )
                _LOGGER.info(
                    "[%s] Using provided encryption key/version directly via %s",
                    self.controller_mac,
                    self._transport,
                )
            else:
                result = await gree_try_bind(
                    self.controller_mac,
                    self._userid,
                    preferred_version,
                    preferred_key,
                    self._transport,
                )

        except Exception:
            _LOGGER.exception("Error while binding")
            await self._transport.unsubscribe(self.controller_mac)
            raise

        _LOGGER.info(
            "[%s] Device is bound with version %s and key %s via %s",
            self.controller_mac,
            result.encryption_version,
            redact_str(result.encryption_key),
            self._transport,
        )

        self._binding = result

        self._cipher = get_cipher(result.encryption_version, result.encryption_key)

        self._transport.add_listener(
            self._mac,
            self._handle_transport_message,
        )

        self._bound = True
        self._available = True

    async def unbind(self) -> None:
        """Unbind from the current transport."""
        if not self._bound:
            return

        if not self._transport:
            raise GreeBindingError("Cannot unbind when no transport is set.")

        self._transport.remove_listener(
            self._mac,
            self._handle_transport_message,
        )

        await self._transport.unsubscribe(
            self.controller_mac,
        )

        self._bound = False
        self._available = False
        self._cipher = None

    async def rebind(self) -> None:
        """Try binding with the current transport and existing binding info."""
        await self.unbind()
        return await self.bind(
            self.controller_mac, self.encryption_version, self.encryption_key
        )

    #
    # Transport
    #

    @property
    def transport(self) -> GreeBaseTransport | None:
        """The current client transport."""
        return self._transport

    async def set_transport(
        self,
        transport: GreeBaseTransport,
    ) -> None:
        """Set the client transport."""
        await self.unbind()
        self._transport = transport

    #
    # Query
    #

    async def query_props(
        self,
        props: list[str],
        request_batch: int = 1,
        error_as_missing: bool = False,
    ) -> tuple[dict[str, str], list[str]]:
        """Query the status value of device properties."""
        if not self._bound:
            await self.rebind()

        if not self._cipher:
            raise GreeRuntimeError("No cipher set.")

        if not self._transport:
            raise GreeRuntimeError("No transport set.")

        state: dict[str, str] = {}
        missing: list[str] = []
        answered = False
        connection_lost = False

        for chunk in chunked(props, request_batch):
            try:
                result = await gree_get_status(
                    self.controller_mac,
                    self._mac,
                    self._userid,
                    chunk,
                    self._cipher,
                    self._transport,
                )

                state.update(result.prop_values)
                missing.extend(result.missing_props)
                answered = True

            except GreeConnectionError:
                # The last known state can no longer be trusted
                self._available = False
                connection_lost = True
                if error_as_missing:
                    missing.extend(chunk)
                else:
                    raise

            except GreeError:
                if error_as_missing:
                    missing.extend(chunk)
                else:
                    raise

        # GREE GMV/VRF cloud child units expose the current indoor
        # temperature as InTem (guarded by InTemEn) instead of TemSen.
        # When TemSen was requested but not returned, query the two VRF
        # fields together so gree_process_status_pack() can normalize
        # InTem -> TemSen for the standard climate entity.
        is_vrf_child = self.controller_mac != self._mac
        if is_vrf_child and GreeProp.SENSOR_INDOOR_TEMPERATURE_3.value in props:
            if GreeProp.SENSOR_INDOOR_TEMPERATURE_3.value not in state:
                try:
                    result = await gree_get_status(
                        self.controller_mac,
                        self._mac,
                        self._userid,
                        ["InTemEn", "InTem"],
                        self._cipher,
                        self._transport,
                    )
                    temsen = result.prop_values.get(
                        GreeProp.SENSOR_INDOOR_TEMPERATURE_3.value
                    )
                    if temsen is not None:
                        state[GreeProp.SENSOR_INDOOR_TEMPERATURE_3.value] = temsen
                        missing = [
                            prop
                            for prop in missing
                            if prop != GreeProp.SENSOR_INDOOR_TEMPERATURE_3.value
                        ]
                except GreeError:
                    # Keep the normal missing-property behaviour if the VRF
                    # fallback is unavailable on this particular unit.
                    pass

        if answered and not connection_lost:
            self._available = True

        return state, missing

    async def query_all_props(
        self,
        request_batch: int = 1,
        error_as_missing: bool = False,
    ) -> tuple[dict[str, str], list[str]]:
        """Query all possible props."""

        all_props = [
            *[prop.value for prop in GreeProp],
            *[prop.value for prop in InfoProp],
            *[prop.value for prop in OtherProps],
        ]

        return await self.query_props(all_props, request_batch, error_as_missing)

    async def set_props(
        self,
        values: Mapping[str, int],
    ) -> None:
        """Send the state of multiple properties to the device."""
        if not self._bound:
            await self.rebind()

        if not self._cipher:
            raise GreeRuntimeError("No cipher set.")

        if not self._transport:
            raise GreeRuntimeError("No transport set.")

        try:
            await gree_set_status(
                self.controller_mac,
                self._mac,
                self._userid,
                values,
                self._cipher,
                self._transport,
            )
        except GreeConnectionError:
            self._available = False
            raise

        self._available = True

    #
    # Transport Push Messages
    #

    def add_status_listener(
        self,
        callback: Callable[[dict[str, str]], None],
    ) -> None:
        """Add a listener for status updates."""
        _LOGGER.debug("Adding Listener: %s", callback)
        self._listeners.append(callback)

    def remove_status_listener(
        self,
        callback: Callable[[dict[str, str]], None],
    ) -> None:
        """Remove a listener from status updates."""
        _LOGGER.debug("Removing Listener: %s", callback)
        try:
            self._listeners.remove(callback)
        except ValueError:
            _LOGGER.warning("Callback to remove not in the listeners list")

    def _handle_transport_message(
        self,
        topic: str,
        payload: dict,
    ) -> None:

        if self._cipher is None:
            return

        if "status" not in topic:
            return

        try:
            response = gree_decrypt_pack(
                payload,
                self._cipher,
            )
        except Exception:  # noqa: BLE001
            _LOGGER.debug(
                "[%s] Could not decrypt pushed status on %s", self._mac, topic
            )
            return

        pack = response.get("pack")
        if not isinstance(pack, dict):
            return

        # A VRF gateway publishes status for all indoor units on the same
        # controller topic. Ignore packs belonging to another unit. Packs
        # with no unit id at all are only trusted for standalone devices.
        target = str(pack.get("sub") or pack.get("mac") or "").lower()
        if target != self._mac.lower() and (
            target or self.controller_mac.lower() != self._mac.lower()
        ):
            _LOGGER.debug(
                "[%s] Ignoring pushed status for '%s' on %s",
                self._mac,
                target,
                topic,
            )
            return

        try:
            result = gree_process_status_pack(pack, None)
        except GreeProtocolError:
            _LOGGER.debug(
                "[%s] Ignoring unusable pushed status: %s",
                self._mac,
                describe_status_pack(pack, None),
            )
            return

        _LOGGER.debug(
            "[%s] Pushed status on %s: %s", self._mac, topic, result.prop_values
        )

        if not result.prop_values:
            return

        for listener in self._listeners:
            try:
                listener(result.prop_values)
            except Exception:
                _LOGGER.exception("Error during listener execution")

    #
    # Properties
    #

    @property
    def available(self) -> bool:
        """Is the device available."""
        return self._available

    @property
    def bound(self) -> bool:
        """Is the device bound to the transport."""
        return self._bound

    @property
    def binding_info(self) -> BindingInfo | None:
        """Binding information for the last successful binding with a transport."""
        return self._binding

    @property
    def encryption_key(self) -> str | None:
        """The current device encryption key obtained after binding."""
        return None if self._binding is None else self._binding.encryption_key

    @property
    def encryption_version(self) -> EncryptionVersion | None:
        """The current device encryption version obtained after binding."""
        return None if self._binding is None else self._binding.encryption_version
