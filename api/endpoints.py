import asyncio
import json
import logging
import time
import uuid
from contextlib import suppress

from fastapi import APIRouter, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from pydantic import ValidationError

from models.schemas import (
    Coordinates,
    DeviceSelection,
    Favorite,
    GPXRequest,
    Movement,
    RandomRouteRequest,
    RouteRequest,
    Speed,
)
from services.geocoder import GeocodingError
from services.gpx_parser import parse_gpx
from services.route_engine import RandomRouteEngine, RouteEngine, generate_random_points

router = APIRouter()
logger = logging.getLogger(__name__)


def controller(request: Request):
    return request.app.state.controller


@router.get("/api/state")
async def state(request: Request):
    c = controller(request)
    return {"location": c.state.snapshot(), "device": c.state.device_snapshot(), "provider": c.mode}


@router.post("/api/movement/distance/reset")
async def reset_distance(request: Request):
    c = controller(request)
    async with c.state.lock:
        c.state.distance_m = 0
        c.broadcast()
        return c.state.snapshot()


@router.get("/api/search")
async def search_places(
    request: Request,
    q: str = Query(min_length=2, max_length=200),
    language: str = Query(default="en", min_length=2, max_length=32),
):
    try:
        return {"results": await controller(request).geocoder.search(q, language)}
    except GeocodingError as exc:
        raise HTTPException(502, str(exc)) from exc


@router.get("/api/devices")
async def devices(request: Request):
    c = controller(request)
    return [
        dict(
            d,
            status=c.state.status
            if c.state.selected_device and d["udid"] == c.state.selected_device["udid"]
            else d["status"],
        )
        for d in c.devices.devices
    ]


@router.post("/api/devices/connect")
async def connect(body: DeviceSelection, request: Request):
    c = controller(request)
    await c.connect(body.udid)
    return c.state.device_snapshot()


@router.post("/api/devices/disconnect")
async def disconnect(request: Request):
    c = controller(request)
    async with c.state.lock:
        await c.state.clear()
        await c.state.provider.disconnect()
        c.state.status, c.state.selected_device = "DISCONNECTED", None
        c.broadcast()
    return {"ok": True}


@router.post("/api/location/set")
async def set_location(body: Coordinates, request: Request):
    c = controller(request)
    async with c.state.lock:
        c.state.stop()
        await c.state.set_position(body)
        c.db.record(body, "Teleport")
        c.broadcast()
    logger.info("Teleport applied")
    return c.state.snapshot()


@router.post("/api/location/run")
async def run_to_location(body: Coordinates, request: Request):
    c = controller(request)
    async with c.state.lock:
        s = c.state
        s.require_connected()
        if not s.position or not s.simulation_active:
            raise ValueError("Set a starting location with Teleport first")
        if s.restore_pending:
            raise ValueError("Restore the pending simulated location before continuing")
        route = RouteEngine([s.position, body])
        s.stop()
        s.route = route
        c.broadcast()
    return route.snapshot()


@router.post("/api/location/clear")
async def clear_location(request: Request):
    c = controller(request)
    async with c.state.lock:
        try:
            await c.state.clear()
        finally:
            c.broadcast()
    return c.state.snapshot()


@router.post("/api/routes/start")
async def start_route(body: RouteRequest, request: Request):
    c = controller(request)
    route = RouteEngine(body.points, body.loops)
    async with c.state.lock:
        c.state.stop()
        await c.state.set_position(body.points[0])
        c.state.route = route
        c.db.record(body.points[0], "Route start")
        c.broadcast()
    logger.info("Route started")
    return route.snapshot()


@router.post("/api/routes/random/preview")
async def preview_random_route(body: RandomRouteRequest, request: Request):
    c = controller(request)
    start_pos = (
        c.state.position
        if (c.state.simulation_active and c.state.position is not None)
        else body.center
    )
    points = generate_random_points(
        center=body.center,
        radius_m=body.radius_m,
        count=body.point_count,
        start=start_pos,
    )
    return {
        "points": [p.model_dump() for p in points],
        "center": body.center.model_dump(),
        "radius_m": body.radius_m,
    }


@router.post("/api/routes/random")
async def start_random_route(body: RandomRouteRequest, request: Request):
    c = controller(request)
    async with c.state.lock:
        c.state.require_connected()
        if c.state.restore_pending:
            raise ValueError("Restore the pending simulated location before continuing")
        start_pos = (
            c.state.position
            if (c.state.simulation_active and c.state.position is not None)
            else body.center
        )
        route = RandomRouteEngine(
            center=body.center,
            radius_m=body.radius_m,
            point_count=body.point_count,
            continuous=body.continuous,
            start_position=start_pos,
            initial_points=body.initial_points,
        )
        c.state.stop()
        if not c.state.position or not c.state.simulation_active:
            await c.state.set_position(route.points[0])
        c.state.route = route
        c.db.record(body.center, f"Random route ({body.radius_m:.0f}m)")
        c.broadcast()
    logger.info("Random route started around %s with radius %.1fm", body.center, body.radius_m)
    return route.snapshot()


@router.post("/api/routes/random/reroll")
async def reroll_random_route(request: Request):
    c = controller(request)
    async with c.state.lock:
        c.state.require_connected()
        route = c.state.route
        if not isinstance(route, RandomRouteEngine):
            raise ValueError("Active route is not a random route")
        if not c.state.position:
            raise ValueError("No active location available")
        route.reroll(c.state.position)
        c.broadcast()
    return route.snapshot()


@router.post("/api/routes/{action}")
async def route_action(action: str, request: Request):
    if action not in ("pause", "resume", "stop"):
        raise HTTPException(404, "Unknown route action")
    c = controller(request)
    async with c.state.lock:
        route = c.state.route
        if not route:
            raise ValueError("No active route")
        if action == "resume":
            c.state.require_connected()
            if route.status != "paused":
                raise ValueError("Only paused routes can resume")
            route.status = "running"
        elif action == "pause":
            if route.status != "running":
                raise ValueError("Only running routes can pause")
            route.status = "paused"
        else:
            route.status = "stopped"
            snap = route.snapshot()
            c.state.route = None
            c.broadcast()
            return snap
        c.broadcast()
    return route.snapshot()


@router.post("/api/gpx/import")
async def import_gpx(body: GPXRequest):
    return {"points": [p.model_dump() for p in parse_gpx(body.xml)]}


@router.get("/api/favorites")
async def favorites(request: Request):
    return controller(request).db.favorites()


@router.post("/api/favorites", status_code=201)
async def add_favorite(body: Favorite, request: Request):
    return {"id": controller(request).db.add_favorite(body)}


@router.delete("/api/favorites/{identifier}")
async def delete_favorite(identifier: int, request: Request):
    if not controller(request).db.delete_favorite(identifier):
        raise HTTPException(404, "Favorite not found")
    return {"ok": True}


@router.get("/api/history")
async def history(request: Request):
    return controller(request).db.history()


@router.websocket("/ws")
async def websocket(ws: WebSocket):
    origin = ws.headers.get("origin")
    if origin and origin not in (
        "http://" + ws.headers.get("host", ""),
        "https://" + ws.headers.get("host", ""),
    ):
        await ws.close(code=1008)
        return
    await ws.accept()
    c = ws.app.state.controller
    identifier = uuid.uuid4().hex
    queue = asyncio.Queue(maxsize=32)
    c.clients[identifier] = queue
    c.broadcast()

    async def sender():
        while True:
            await asyncio.wait_for(ws.send_json(await queue.get()), 5)

    async def receiver():
        while True:
            raw = await ws.receive_text()
            try:
                if len(raw) > 2048:
                    raise ValueError("Message too large")
                data = json.loads(raw)
                if not isinstance(data, dict):
                    raise ValueError("Expected an object")
                message = (
                    Speed.model_validate(data)
                    if data.get("type") == "speed"
                    else Movement.model_validate(data)
                )
                async with c.state.lock:
                    s = c.state
                    s.require_connected()
                    if isinstance(message, Speed):
                        s.speed_schedule = message.schedule
                        s.speed_schedule_elapsed = 0
                        s.speed_kmh = 5 if message.schedule == "target10k" else message.kmh
                        logger.info("Speed changed to %.1f km/h", message.kmh)
                    elif message.active:
                        if s.position is None:
                            raise ValueError("Teleport to a starting location first")
                        if s.owner and s.owner != identifier:
                            raise ValueError("Joystick is controlled by another browser")
                        s.stop()
                        s.moving, s.bearing, s.owner = True, message.bearing, identifier
                        s.last_input = time.monotonic()
                    elif s.owner == identifier:
                        s.moving, s.owner = False, None
                    c.broadcast()
            except (ValueError, ValidationError, ConnectionError) as exc:
                if queue.full():
                    queue.get_nowait()
                queue.put_nowait({"type": "error", "code": "INVALID_COMMAND", "message": str(exc)})

    tasks = [asyncio.create_task(sender()), asyncio.create_task(receiver())]
    try:
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            task.result()
    except (WebSocketDisconnect, RuntimeError, TimeoutError):
        pass
    finally:
        for task in tasks:
            task.cancel()
        for task in tasks:
            with suppress(asyncio.CancelledError, WebSocketDisconnect, RuntimeError, TimeoutError):
                await task
        c.clients.pop(identifier, None)
        async with c.state.lock:
            if c.state.owner == identifier:
                c.state.moving, c.state.owner = False, None
            if not c.clients:
                c.state.stop()
