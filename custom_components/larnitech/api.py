"""Client for the Larnitech API2 (WebSocket, local push).

Protocol (wiki.larnitech.com/API2): one JSON object per WebSocket message.
  -> {"request": "authorize", "key": "..."}
  -> {"request": "get-devices", "status": "detailed"}
  -> {"request": "status-get", "addr": "158:240", "status": "detailed"}
  -> {"request": "status-set", "addr": "585:17", "status": {"level": 10}}
  -> {"request": "status-subscribe", "addr": "999:250"}
Replies carry "response": "<request name>". Requests have no id, so they are
sent one at a time. Any other message that carries devices with a status is a
pushed status change.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import Callable
from typing import Any

import aiohttp

_LOGGER = logging.getLogger(__name__)

REQUEST_TIMEOUT = 15
HEARTBEAT = 30
RECONNECT_MIN = 2
RECONNECT_MAX = 60


class LarnitechError(Exception):
    """Generic API error."""


class LarnitechAuthError(LarnitechError):
    """Wrong API key."""


class LarnitechConnectionError(LarnitechError):
    """Controller unreachable."""


class LarnitechClient:
    """Keeps one WebSocket to the controller, reconnects on loss."""

    def __init__(self, session: aiohttp.ClientSession, host: str, port: int, key: str) -> None:
        self._session = session
        self.host = host
        self.port = port
        self._key = key
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._lock = asyncio.Lock()
        self._pending: tuple[str, asyncio.Future] | None = None
        self._reader: asyncio.Task | None = None
        self._runner: asyncio.Task | None = None
        self._closing = False
        self._refresh_pending: set[str] = set()
        self.devices: dict[str, dict[str, Any]] = {}
        # addresses of elements this integration created for Home Assistant entities
        self.owned: set[str] = set()
        self.connected = False
        self._status_listeners: list[Callable[[str, dict[str, Any]], None]] = []
        self._conn_listeners: list[Callable[[bool], None]] = []
        # plugin API (Home Assistant entities registered as Larnitech elements)
        self._plugin_listeners: list[Callable[[dict[str, Any]], None]] = []
        # coroutines run after every (re)authorization, e.g. plugin setup
        self._session_listeners: list[Callable[[], Any]] = []

    @property
    def url(self) -> str:
        scheme = "wss" if self.port == 8443 else "ws"
        return f"{scheme}://{self.host}:{self.port}/api"

    # ---- listeners -------------------------------------------------------
    def add_status_listener(self, cb: Callable[[str, dict[str, Any]], None]) -> Callable[[], None]:
        self._status_listeners.append(cb)
        return lambda: self._status_listeners.remove(cb)

    def add_connection_listener(self, cb: Callable[[bool], None]) -> Callable[[], None]:
        self._conn_listeners.append(cb)
        return lambda: self._conn_listeners.remove(cb)

    def add_plugin_listener(self, cb: Callable[[dict[str, Any]], None]) -> Callable[[], None]:
        """Plugin API messages: she-device-status, she-device-is-created, she-register-pnp results."""
        self._plugin_listeners.append(cb)
        return lambda: self._plugin_listeners.remove(cb)

    def add_session_listener(self, cb: Callable[[], Any]) -> Callable[[], None]:
        self._session_listeners.append(cb)
        return lambda: self._session_listeners.remove(cb)

    def _set_connected(self, value: bool) -> None:
        if self.connected == value:
            return
        self.connected = value
        for cb in list(self._conn_listeners):
            cb(value)

    # ---- connection ------------------------------------------------------
    async def connect(self) -> None:
        """Open the socket and authorize. Raises on failure."""
        try:
            self._ws = await self._session.ws_connect(
                self.url, heartbeat=HEARTBEAT, timeout=aiohttp.ClientWSTimeout(ws_close=10)
            )
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as err:
            raise LarnitechConnectionError(f"Cannot connect to {self.url}: {err}") from err
        self._reader = asyncio.create_task(self._read_loop(self._ws))
        try:
            reply = await self.request({"request": "authorize", "key": self._key})
        except LarnitechError:
            await self._drop()
            raise
        # the controller answers {"response": "authorize", "result": "success"}
        if _is_error(reply) or str(reply.get("result", "success")).lower() != "success":
            await self._drop()
            raise LarnitechAuthError(_error_text(reply))
        self._set_connected(True)
        for cb in list(self._session_listeners):
            await cb()

    async def start(self) -> None:
        """Connect, load devices, subscribe, keep the link alive in background."""
        await self.connect()
        await self.load_devices()
        await self.subscribe_all()
        self._runner = asyncio.create_task(self._keepalive())

    async def close(self) -> None:
        self._closing = True
        if self._runner:
            self._runner.cancel()
        await self._drop()

    async def _drop(self) -> None:
        ws, self._ws = self._ws, None
        if self._reader and self._reader is not asyncio.current_task():
            self._reader.cancel()
        if ws is not None and not ws.closed:
            with contextlib.suppress(Exception):
                await ws.close()
        if self._pending:
            _, fut = self._pending
            if not fut.done():
                fut.set_exception(LarnitechConnectionError("connection closed"))
        self._set_connected(False)

    async def _keepalive(self) -> None:
        delay = RECONNECT_MIN
        while not self._closing:
            if self._ws is not None and not self._ws.closed:
                await asyncio.sleep(5)
                continue
            self._set_connected(False)
            try:
                await self.connect()
                await self.load_devices()
                await self.subscribe_all()
                _LOGGER.info("Reconnected to Larnitech controller %s", self.host)
                delay = RECONNECT_MIN
            except LarnitechError as err:
                _LOGGER.debug("Reconnect to %s failed: %s", self.host, err)
                await self._drop()
                await asyncio.sleep(delay)
                delay = min(delay * 2, RECONNECT_MAX)

    async def _read_loop(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        try:
            async for msg in ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    self._on_text(msg.data)
                elif msg.type == aiohttp.WSMsgType.BINARY:
                    self._on_text(msg.data.decode("utf-8", "replace"))
                elif msg.type in (aiohttp.WSMsgType.ERROR, aiohttp.WSMsgType.CLOSE):
                    break
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Larnitech read loop failed")
        finally:
            if self._ws is ws:
                self._ws = None
                if self._pending and not self._pending[1].done():
                    self._pending[1].set_exception(LarnitechConnectionError("connection lost"))
                self._set_connected(False)

    def _on_text(self, text: str) -> None:
        text = text.strip()
        if text.startswith("-JSON-"):
            text = text[6:]
        try:
            data = json.loads(text)
        except ValueError:
            _LOGGER.debug("Non-JSON message from controller: %s", text[:200])
            return
        if not isinstance(data, dict):
            return
        name = data.get("response")
        event = data.get("event")
        if event == "statuses":
            self._handle_push(data)
            return
        if (isinstance(event, str) and event.startswith("she-")) or (
            name == "she-register-pnp" and any(k in data for k in ("register", "unregister", "error")) and "status" not in data
        ):
            # plugin API: commands for our elements and the second, final reply to she-register-pnp
            for cb in list(self._plugin_listeners):
                cb(data)
            return
        if self._pending and not self._pending[1].done():
            expected, fut = self._pending
            if name == expected or (name is None and _is_error(data) and "event" not in data):
                fut.set_result(data)
                return
        self._handle_push(data)

    def _handle_push(self, data: dict[str, Any]) -> None:
        devices = data.get("devices")
        if isinstance(devices, dict):
            devices = [devices]
        if not isinstance(devices, list):
            if "addr" in data and "status" in data:
                devices = [data]
            else:
                return
        for dev in devices:
            if isinstance(dev, dict) and "addr" in dev and "status" in dev:
                self._apply_status(str(dev["addr"]), dev["status"])

    def _apply_status(self, addr: str, status: Any) -> None:
        if isinstance(status, str):
            # compact hex status ("0x08"): ask for the readable form
            self._schedule_refresh(addr)
            return
        if not isinstance(status, dict):
            return
        dev = self.devices.get(addr)
        if dev is None:
            return
        merged = dict(dev.get("status") or {})
        merged.update(status)
        dev["status"] = merged
        for cb in list(self._status_listeners):
            cb(addr, merged)

    def _schedule_refresh(self, addr: str) -> None:
        if addr in self._refresh_pending or addr not in self.devices:
            return
        self._refresh_pending.add(addr)

        async def _do() -> None:
            await asyncio.sleep(0.05)
            self._refresh_pending.discard(addr)
            with contextlib.suppress(LarnitechError):
                await self.refresh(addr)

        asyncio.create_task(_do())

    # ---- requests --------------------------------------------------------
    async def request(self, payload: dict[str, Any]) -> dict[str, Any]:
        async with self._lock:
            ws = self._ws
            if ws is None or ws.closed:
                raise LarnitechConnectionError("not connected")
            fut: asyncio.Future = asyncio.get_running_loop().create_future()
            self._pending = (payload["request"], fut)
            try:
                await ws.send_str(json.dumps(payload, ensure_ascii=False))  # the controller keeps \u escapes literally
                async with asyncio.timeout(REQUEST_TIMEOUT):
                    return await fut
            except (aiohttp.ClientError, ConnectionResetError) as err:
                raise LarnitechConnectionError(str(err)) from err
            except TimeoutError as err:
                raise LarnitechConnectionError(f"no reply to {payload['request']}") from err
            finally:
                self._pending = None

    async def load_devices(self) -> dict[str, dict[str, Any]]:
        reply = await self.request({"request": "get-devices", "status": "detailed"})
        if _is_error(reply):
            raise LarnitechError(_error_text(reply))
        found: dict[str, dict[str, Any]] = {}
        for dev in reply.get("devices") or []:
            if not isinstance(dev, dict) or "addr" not in dev:
                continue
            addr = str(dev["addr"])
            if not isinstance(dev.get("status"), dict):
                dev["status"] = {}
            if addr in self.owned:
                dev["ha-owned"] = True
            found[addr] = dev
        if self.devices:
            # keep the same dict objects so entities keep their reference
            for addr, dev in found.items():
                if addr in self.devices:
                    self.devices[addr].clear()
                    self.devices[addr].update(dev)
                    for cb in list(self._status_listeners):
                        cb(addr, dev["status"])
                else:
                    self.devices[addr] = dev
        else:
            self.devices = found
        return self.devices

    async def subscribe_all(self) -> None:
        for addr in list(self.devices):
            reply = await self.request({"request": "status-subscribe", "addr": addr, "status": "detailed"})
            if _is_error(reply):
                _LOGGER.debug("Subscribe %s failed: %s", addr, _error_text(reply))

    async def refresh(self, addr: str) -> None:
        reply = await self.request({"request": "status-get", "addr": addr, "status": "detailed"})
        for dev in reply.get("devices") or []:
            if isinstance(dev, dict) and isinstance(dev.get("status"), dict):
                self._apply_status(str(dev.get("addr", addr)), dev["status"])

    async def set_status(self, addr: str, status: dict[str, Any] | str) -> None:
        reply = await self.request({"request": "status-set", "addr": addr, "status": status})
        if _is_error(reply):
            raise LarnitechError(_error_text(reply))
        for dev in reply.get("devices") or []:
            if isinstance(dev, dict) and dev.get("success") is False:
                raise LarnitechError(f"controller refused command for {addr}")
        # optimistic update; the pushed event will confirm it
        if isinstance(status, dict):
            self._apply_status(addr, status)


def _is_error(reply: dict[str, Any]) -> bool:
    return "error" in reply or reply.get("success") is False or reply.get("result") == "ERROR"


def _error_text(reply: dict[str, Any]) -> str:
    err = reply.get("error") or reply.get("message") or reply
    if isinstance(err, dict):
        err = err.get("message") or err.get("text") or json.dumps(err)
    return str(err)
