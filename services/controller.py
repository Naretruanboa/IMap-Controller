import asyncio
import logging
import os
import time
from contextlib import suppress

from database.database import Database
from models.state import AppState
from services.android_location import AndroidLocationProvider
from services.device_manager import DeviceManager
from services.geocoder import Geocoder
from services.ios_location import MockLocationProvider, PymobiledeviceLocationProvider
from services.movement_engine import destination

logger = logging.getLogger(__name__)


class Controller:
    def __init__(self, mode: str, database: str, hz: float, speed: float):
        self.mode, self.hz = mode, hz
        self.default_speed = speed
        self.device_speeds: dict[str, float] = {}
        self.monitor_interval = 3.0
        providers = {
            "mock": MockLocationProvider,
            "iphone": PymobiledeviceLocationProvider,
            "android": AndroidLocationProvider,
        }
        self.state = AppState(providers[mode](), speed)
        self.devices = DeviceManager(mode)
        self.db = Database(database)
        self.geocoder = Geocoder(os.getenv("GEOCODER_URL", "https://nominatim.openstreetmap.org"))
        self.clients: dict[str, asyncio.Queue] = {}
        self.tasks: list[asyncio.Task] = []

    def publish(self, payload: dict) -> None:
        for queue in self.clients.values():
            if queue.full():
                queue.get_nowait()
            queue.put_nowait(payload)

    def broadcast(self) -> None:
        self.publish(self.state.snapshot())
        self.publish(self.state.device_snapshot())
        self.publish(
            self.state.route.snapshot() if self.state.route else {"type": "route_state", "status": "idle"}
        )

    async def start(self) -> None:
        await self.devices.scan()
        self.tasks = [asyncio.create_task(self.movement_loop()), asyncio.create_task(self.monitor())]

    async def connect(self, udid: str) -> None:
        state = self.state
        device = next((d for d in self.devices.devices if d["udid"] == udid), None)
        if not device:
            raise ValueError("Device not found. Refresh USB devices.")
        async with state.lock:
            await state.clear()
            await state.provider.disconnect()
            if state.selected_device:
                self.device_speeds[state.selected_device["udid"]] = state.speed_kmh
            state.speed_kmh = self.device_speeds.get(udid, self.default_speed)
            state.route = None
            if not state.selected_device or state.selected_device["udid"] != udid:
                state.distance_m = 0
            state.selected_device = device.copy()
            state.status = "CONNECTING"
            self.broadcast()
            try:
                await asyncio.wait_for(state.provider.connect(device), 15)
                state.status = "CONNECTED"
                logger.info("Device connected")
            except Exception as exc:
                await state.failed()
                raise ConnectionError("Developer connection failed: " + str(exc)) from None
            finally:
                self.broadcast()

    async def movement_loop(self) -> None:
        previous = time.monotonic()
        while True:
            await asyncio.sleep(1 / self.hz)
            now = time.monotonic()
            dt, previous = min(now - previous, 0.5), now
            async with self.state.lock:
                s = self.state
                if s.moving and now - s.last_input > 1:
                    s.moving, s.owner = False, None
                if s.status == "CONNECTED" and s.position:
                    active = s.moving or bool(s.route and s.route.status == "running")
                    meters = s.movement_budget(dt) if active else 0
                    point = None
                    travelled = 0.0
                    if s.route and s.route.status == "running":
                        before = s.route.travelled
                        point, s.bearing = s.route.advance(s.position, meters)
                        travelled = s.route.travelled - before
                        if s.route.status == "completed":
                            logger.info("Route finished")
                    elif s.moving:
                        point = destination(s.position, s.bearing, meters)
                        travelled = meters
                    if point:
                        try:
                            await s.set_position(point)
                            s.distance_m += travelled
                            self.db.save_last_location(point, s.bearing)
                        except ConnectionError as exc:
                            self.publish({"type": "error", "code": "LOCATION_FAILED", "message": str(exc)})
                self.broadcast()

    async def monitor(self) -> None:
        while True:
            await asyncio.sleep(self.monitor_interval)
            await self.devices.scan()
            async with self.state.lock:
                s = self.state
                if not s.selected_device:
                    continue
                found = next(
                    (d for d in self.devices.devices if d["udid"] == s.selected_device["udid"]), None
                )
                if not found:
                    if s.status == "CONNECTED":
                        await s.failed()
                        self.publish(
                            {"type": "error", "code": "DEVICE_DISCONNECTED", "message": "iPhone disconnected"}
                        )
                    s.status = "DISCONNECTED"
                elif s.status in ("ERROR", "DISCONNECTED"):
                    s.status = "CONNECTING"
                    self.broadcast()
                    try:
                        await asyncio.wait_for(s.provider.connect(found), 15)
                        s.status = "CONNECTED"
                        # Restore any uncertain simulation before accepting fresh commands.
                        await s.clear()
                        logger.info("Device reconnected")
                    except Exception:
                        await s.failed()
                self.broadcast()

    async def close(self) -> None:
        for task in self.tasks:
            task.cancel()
        for task in self.tasks:
            with suppress(asyncio.CancelledError):
                await task
        try:
            await self.state.clear()
        except Exception:
            logger.error("Shutdown restore failed. Reconnect and restore, or restart the iPhone.")
        finally:
            with suppress(Exception):
                await asyncio.wait_for(self.state.provider.disconnect(), 5)
            await self.geocoder.close()
            self.db.close()
