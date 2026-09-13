import asyncio

import pytest

from models.schemas import Coordinates
from models.state import AppState
from services.controller import Controller
from services.ios_location import MockLocationProvider


class FailingProvider(MockLocationProvider):
    async def set_location(self, lat, lon):
        raise ConnectionError("USB removed")


async def test_provider_failure_stops_movement():
    s = AppState(FailingProvider(), status="CONNECTED", moving=True)
    with pytest.raises(ConnectionError):
        await s.set_position(Coordinates(latitude=0, longitude=0))
    assert s.status == "ERROR"
    assert not s.moving
    assert s.restore_pending


async def test_clear_failure_keeps_pending():
    class FailClear(MockLocationProvider):
        async def clear_location(self):
            raise ConnectionError("gone")

    s = AppState(FailClear(), status="CONNECTED", simulation_active=True, moving=True)
    with pytest.raises(ConnectionError):
        await s.clear()
    assert s.restore_pending and s.simulation_active and not s.moving


async def test_worker_uses_provider_and_shuts_down(tmp_path):
    c = Controller("mock", str(tmp_path / "app.db"), 20, 5)
    await c.start()
    try:
        await c.connect("mock-iphone")
        await c.state.set_position(Coordinates(latitude=0, longitude=0))
        c.state.moving = True
        await asyncio.sleep(0.2)
        assert c.state.position.latitude > 0
        assert c.state.provider.location[0] == c.state.position.latitude
        assert len(c.tasks) == 2
    finally:
        await c.close()


def test_target_schedule_covers_10km_in_60_minutes():
    s = AppState(MockLocationProvider(), speed_schedule="target10k")
    meters = sum(s.movement_budget(0.5) for _ in range(7200))
    assert meters == pytest.approx(10000)
    assert s.speed_schedule_elapsed == 3600
    assert s.movement_budget(3600) == pytest.approx(10000)
    assert s.speed_schedule_elapsed == 7200


def test_target_schedule_alternates_and_integrates_phase_boundary():
    s = AppState(MockLocationProvider(), speed_schedule="target10k")
    assert s.movement_budget(29) == pytest.approx((5 + 15 - 1 / 3) / 2 / 3.6 * 29)
    assert s.movement_budget(2) == pytest.approx((15 + 15 - 1 / 3) / 3.6)
    assert s.speed_kmh == pytest.approx(15 - 1 / 3)
    s.movement_budget(29)
    assert s.speed_kmh == 5


async def test_clear_resets_target_schedule():
    s = AppState(MockLocationProvider(), speed_schedule="target10k", speed_schedule_elapsed=100)
    await s.clear()
    assert s.speed_schedule == "off"
    assert s.speed_schedule_elapsed == 0


async def test_target_schedule_continues_route_after_60_minutes(tmp_path):
    from services.route_engine import RouteEngine

    c = Controller("mock", str(tmp_path / "target.db"), 20, 5)
    await c.start()
    try:
        await c.connect("mock-iphone")
        start = Coordinates(latitude=0, longitude=0)
        await c.state.set_position(start)
        c.state.route = RouteEngine([start, Coordinates(latitude=1, longitude=0)])
        c.state.speed_schedule = "target10k"
        c.state.speed_schedule_elapsed = 3599.99
        await asyncio.sleep(0.15)
        assert c.state.speed_schedule_elapsed > 3600
        assert c.state.speed_schedule == "target10k"
        assert c.state.route.status == "running"
        assert c.state.route.travelled > (5 + 0.01 / 6) / 3.6 * 0.01
    finally:
        await c.close()


def test_target_speed_changes_gradually_through_entire_cycle():
    s = AppState(MockLocationProvider(), speed_schedule="target10k", speed_kmh=5)
    for tick in range(1, 601):
        previous = s.speed_kmh
        s.movement_budget(0.1)
        assert 5 <= s.speed_kmh <= 15
        assert abs(s.speed_kmh - previous) <= 10 / 30 * 0.1 + 1e-10
        if tick == 150:
            assert s.speed_kmh == pytest.approx(10)
        if tick == 300:
            assert s.speed_kmh == pytest.approx(15)
    assert s.speed_kmh == pytest.approx(5)


@pytest.mark.parametrize("fail", [False, True])
async def test_distance_counts_route_segments_only_after_success(tmp_path, fail):
    from services.movement_engine import destination
    from services.route_engine import RouteEngine

    c = Controller("mock", str(tmp_path / "distance.db"), 20, 50)
    await c.devices.scan()
    await c.connect("mock-iphone")
    start = Coordinates(latitude=0, longitude=0)
    await c.state.set_position(start)
    # A short looping route crosses multiple corners within one movement tick.
    c.state.route = RouteEngine([start, destination(start, 0, 0.1)], loops=0)
    if fail:
        c.state.provider = FailingProvider()
    worker = asyncio.create_task(c.movement_loop())
    try:
        await asyncio.sleep(0.18)
        if fail:
            assert c.state.distance_m == 0
            assert c.state.status == "ERROR"
        else:
            assert c.state.distance_m > 1
            assert c.state.distance_m == pytest.approx(c.state.route.travelled)
    finally:
        worker.cancel()
        with pytest.raises(asyncio.CancelledError):
            await worker
        await c.close()
