import asyncio
import logging
import time
from dataclasses import dataclass, field

from models.schemas import Coordinates
from services.ios_location import LocationProvider
from services.route_engine import RouteEngine

logger = logging.getLogger(__name__)


@dataclass
class AppState:
    provider: LocationProvider
    speed_kmh: float = 5
    speed_schedule: str = "off"
    speed_schedule_elapsed: float = 0
    distance_m: float = 0
    selected_device: dict | None = None
    status: str = "DISCONNECTED"
    position: Coordinates | None = None
    bearing: float = 0
    moving: bool = False
    simulation_active: bool = False
    restore_pending: bool = False
    route: RouteEngine | None = None
    owner: str | None = None
    last_input: float = field(default_factory=time.monotonic)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def snapshot(self) -> dict:
        return {
            "type": "location_state",
            "latitude": self.position.latitude if self.position else None,
            "longitude": self.position.longitude if self.position else None,
            "speed_kmh": self.speed_kmh,
            "distance_m": self.distance_m,
            "speed_schedule": self.speed_schedule,
            "speed_schedule_elapsed": self.speed_schedule_elapsed,
            "bearing": self.bearing,
            "moving": self.moving or bool(self.route and self.route.status == "running"),
            "simulation_active": self.simulation_active,
            "restore_pending": self.restore_pending,
        }

    def device_snapshot(self) -> dict:
        return {
            "type": "device_state",
            "device": self.selected_device,
            "status": self.status,
            "connected": self.status == "CONNECTED",
        }

    def movement_budget(self, dt: float) -> float:
        if self.speed_schedule != "target10k":
            return self.speed_kmh / 3.6 * dt
        end = self.speed_schedule_elapsed + dt
        meters = 0.0
        while self.speed_schedule_elapsed < end:
            phase = int(self.speed_schedule_elapsed // 30)
            boundary = min(end, (phase + 1) * 30)
            # Integrate each linear ramp exactly, including ticks across a turning point.
            start_speed = self.target_speed(self.speed_schedule_elapsed)
            end_speed = self.target_speed(boundary)
            meters += (start_speed + end_speed) / 2 / 3.6 * (boundary - self.speed_schedule_elapsed)
            self.speed_schedule_elapsed = boundary
        self.speed_kmh = self.target_speed(end)
        return meters

    @staticmethod
    def target_speed(elapsed: float) -> float:
        # One minute cycle: 5 -> 15 over 30 seconds, then 15 -> 5.
        return 5.0 + (10.0 / 30.0) * (30.0 - abs(elapsed % 60.0 - 30.0))

    def stop(self) -> None:
        self.moving = False
        self.owner = None
        if self.route and self.route.status in ("running", "paused"):
            self.route.status = "stopped"
        self.route = None

    def require_connected(self) -> None:
        if self.status != "CONNECTED":
            raise ConnectionError("Device disconnected. Connect a device first.")

    async def set_position(self, point: Coordinates) -> None:
        self.require_connected()
        if self.restore_pending:
            raise ConnectionError("Restore the pending simulated location before continuing.")
        try:
            self.restore_pending = True
            await asyncio.wait_for(self.provider.set_location(point.latitude, point.longitude), 5)
        except Exception:
            self.restore_pending = True
            await self.failed()
            raise ConnectionError("Location simulation failed. Check USB and Developer Mode.") from None
        self.restore_pending = False
        self.position = point
        self.simulation_active = True

    async def clear(self) -> None:
        self.stop()
        self.speed_schedule = "off"
        self.speed_schedule_elapsed = 0
        if self.simulation_active or self.restore_pending:
            try:
                await asyncio.wait_for(self.provider.clear_location(), 5)
            except Exception:
                self.restore_pending = True
                await self.failed()
                raise ConnectionError("Restore failed; reconnect this device to retry.") from None
        self.simulation_active = False
        self.restore_pending = False
        self.position = None
        logger.info("Simulation cleared")

    async def failed(self) -> None:
        self.stop()
        self.status = "ERROR"
        self.restore_pending = self.restore_pending or self.simulation_active
        try:
            await asyncio.wait_for(self.provider.disconnect(), 3)
        except Exception:
            pass
        logger.warning("Device session lost; reconnect pending")
