"""Handles network connections."""

from abc import ABC, abstractmethod
from collections import Counter, defaultdict
from collections.abc import Callable
import json
import logging
from typing import Any

from .cipher import CipherBase
from .helpers import gree_decrypt_pack, gree_encrypt_pack

_LOGGER = logging.getLogger(__name__)

# Pack type a device answers with, for each request pack type
_RESPONSE_TYPES = {"status": "dat", "cmd": "res", "bind": "bindok"}


def _response_matcher(
    request_pack: Any, cipher: CipherBase
) -> Callable[[str], bool] | None:
    """Build a check that a raw response answers the given request pack.

    Some gateways send every response twice. Without this check the second
    copy of a command answer is taken as the answer to the next request.
    """
    if not isinstance(request_pack, dict):
        return None

    expected = _RESPONSE_TYPES.get(str(request_pack.get("t")))
    if expected is None:
        return None

    requested_cols = set(request_pack.get("cols") or [])

    def accept(raw: str) -> bool:
        try:
            response = gree_decrypt_pack(json.loads(raw), cipher)
        except Exception:  # noqa: BLE001
            return False

        pack = response.get("pack")
        if not isinstance(pack, dict):
            return False

        # A pack without a type is not rejected, so untyped answers still work
        if pack.get("t") is not None and pack.get("t") != expected:
            return False

        if requested_cols:
            cols = pack.get("cols") or []
            # An empty answer is a valid "nothing supported" answer
            return not cols or bool(requested_cols & set(cols))

        return True

    return accept


class GreeBaseTransport(ABC):
    """Base transport interface."""

    batch_support: bool = False

    def __init__(self) -> None:
        """Init transport."""
        self._listeners: dict[str, set[Callable[[str, dict], None]]] = defaultdict(set)
        self.connected_devices: Counter[str] = Counter()
        # Most props per status request each controller answered, learned when
        # a larger request went unanswered
        self.status_batch_limits: dict[str, int] = {}

    @abstractmethod
    async def connect(self) -> None:
        """Establish connection to endpoint."""

    @abstractmethod
    async def disconnect(self) -> None:
        """Terminate connection to endpoint."""

    @abstractmethod
    async def subscribe(self, mac_controller: str) -> None:
        """Subscribe the transport to a device."""

    @abstractmethod
    async def unsubscribe(self, mac_controller: str) -> None:
        """Unsubscribe the transport from a device."""

    @abstractmethod
    async def request(
        self,
        mac_controller: str,
        json_str: str,
        accept: Callable[[str], bool] | None = None,
    ) -> str:
        """Send raw bytes and return the response.

        `accept` tells whether a raw response answers this request. Transports
        that can receive unrelated or duplicate responses (MQTT) skip the ones
        it rejects and keep waiting.
        """

    def add_listener(
        self, target_mac: str, listener: Callable[[str, dict], None]
    ) -> None:
        """Register a listener for messages for a given device. Callback has the message type and data."""
        self._listeners[target_mac].add(listener)

    def remove_listener(
        self, target_mac: str, listener: Callable[[str, dict], None]
    ) -> None:
        """Unregister a listener for messages for a given device. Callback has the message type and data."""
        listeners = self._listeners.get(target_mac)
        if listeners is None:
            return

        listeners.discard(listener)

        if not listeners:
            del self._listeners[target_mac]

    async def request_json(
        self, mac_controller: str, payload: dict[str, Any], cipher: CipherBase
    ) -> dict[str, Any]:
        """Send and receive a JSON payload."""

        requests: list[dict[str, Any]]

        pack = payload.get("pack")
        if (
            pack
            and not self.batch_support
            and pack.get("t") == "cmd"
            and len(pack.get("opt", [])) > 1
        ):
            requests = []

            for opt, value in zip(pack["opt"], pack["p"], strict=True):
                request = payload.copy()
                request["pack"] = {
                    **pack,
                    "opt": [opt],
                    "p": [value],
                }
                requests.append(request)
        else:
            requests = [payload]

        responses: list[dict[str, Any]] = []

        for request in requests:
            # Built before encrypting: encryption replaces the pack in place
            accept = _response_matcher(request.get("pack"), cipher)
            request = gree_encrypt_pack(request, cipher)

            raw_request = json.dumps(request)
            raw_response = await self.request(mac_controller, raw_request, accept)

            response = json.loads(raw_response)
            response = gree_decrypt_pack(response, cipher)

            responses.append(response)

        if len(responses) == 1:
            return responses[0]

        # Merge responses
        merged = responses[-1].copy()
        merged_pack: dict[str, Any] = {}

        for response in responses:
            pack = response.get("pack")
            if not isinstance(pack, dict):
                continue

            for key, value in pack.items():
                if isinstance(value, list):
                    merged_pack.setdefault(key, []).extend(value)
                else:
                    merged_pack[key] = value

        if merged_pack:
            merged["pack"] = merged_pack
        else:
            merged.pop("pack", None)

        return merged
