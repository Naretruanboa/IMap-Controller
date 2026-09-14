import pytest
from fastapi.testclient import TestClient

from app import create_app
from models.schemas import Coordinates
from services.movement_engine import distance
from services.route_engine import (
    RandomRouteEngine,
    generate_random_point_in_radius,
    generate_random_points,
)

CENTER = Coordinates(latitude=13.7563, longitude=100.5018)


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app("mock", str(tmp_path / "test.db"))) as client:
        yield client


def test_random_point_generation():
    center = CENTER
    radius = 500.0
    for _ in range(50):
        pt = generate_random_point_in_radius(center, radius)
        d = distance(center, pt)
        assert d <= radius + 1.0  # within radius (allowing slight geodesic tolerance)


def test_generate_random_points_list():
    center = CENTER
    radius = 300.0
    points = generate_random_points(center, radius, count=6, start=center)
    assert len(points) == 6
    assert points[0] == center
    for pt in points:
        assert distance(center, pt) <= radius + 1.0


def test_random_route_engine_continuous():
    engine = RandomRouteEngine(center=CENTER, radius_m=200.0, point_count=3, continuous=True)
    assert engine.cycle_count == 1
    assert engine.status == "running"
    assert len(engine.points) == 3

    # Advance through the entire first cycle
    first_cycle_total = sum(distance(a, b) for a, b in zip(engine.points, engine.points[1:]))
    pos = engine.points[0]
    
    # Advance past the first cycle
    new_pos, _ = engine.advance(pos, first_cycle_total + 10.0)
    
    # Check that cycle auto-renewed
    assert engine.cycle_count == 2
    assert engine.status == "running"
    assert len(engine.points) == 3
    assert engine.completed_loops == 1
    assert distance(CENTER, new_pos) <= 200.0 + 1.0

    snap = engine.snapshot()
    assert snap["route_type"] == "random"
    assert snap["cycle"] == 2
    assert snap["continuous"] is True
    assert len(snap["points"]) == 3


def test_random_route_engine_non_continuous():
    engine = RandomRouteEngine(center=CENTER, radius_m=200.0, point_count=3, continuous=False)
    assert engine.continuous is False
    total = sum(distance(a, b) for a, b in zip(engine.points, engine.points[1:]))
    pos = engine.points[0]
    new_pos, _ = engine.advance(pos, total + 50.0)
    assert engine.status == "completed"
    assert new_pos == engine.points[-1]


def test_random_route_reroll():
    engine = RandomRouteEngine(center=CENTER, radius_m=200.0, point_count=4, continuous=True)
    old_points = list(engine.points)
    current_pos = Coordinates(latitude=13.7565, longitude=100.5020)
    engine.reroll(current_pos)
    assert engine.cycle_count == 2
    assert engine.points[0] == current_pos
    assert len(engine.points) == 4


def test_random_route_validation():
    with pytest.raises(ValueError):
        RandomRouteEngine(center=CENTER, radius_m=5.0)  # < 10m
    with pytest.raises(ValueError):
        RandomRouteEngine(center=CENTER, radius_m=100.0, point_count=1)  # < 2 points


def test_api_random_route(client):
    # Before connect
    res = client.post(
        "/api/routes/random",
        json={"center": {"latitude": 13.7563, "longitude": 100.5018}, "radius_m": 500, "point_count": 5},
    )
    assert res.status_code == 503  # Disconnected

    # Connect device
    assert client.post("/api/devices/connect", json={"udid": "mock-iphone"}).status_code == 200

    # Start random route
    res = client.post(
        "/api/routes/random",
        json={
            "center": {"latitude": 13.7563, "longitude": 100.5018},
            "radius_m": 500,
            "point_count": 5,
            "continuous": True,
        },
    )
    assert res.status_code == 200
    data = res.json()
    assert data["route_type"] == "random"
    assert data["status"] == "running"
    assert data["cycle"] == 1
    assert len(data["points"]) == 5

    # Reroll
    reroll_res = client.post("/api/routes/random/reroll")
    assert reroll_res.status_code == 200
    assert reroll_res.json()["cycle"] == 2

    # Pause, resume, stop
    assert client.post("/api/routes/pause").json()["status"] == "paused"
    assert client.post("/api/routes/resume").json()["status"] == "running"
    assert client.post("/api/routes/stop").json()["status"] == "stopped"


def test_api_random_route_preview(client):
    res = client.post(
        "/api/routes/random/preview",
        json={
            "center": {"latitude": 13.7563, "longitude": 100.5018},
            "radius_m": 400,
            "point_count": 6,
        },
    )
    assert res.status_code == 200
    data = res.json()
    assert len(data["points"]) == 6
    assert data["radius_m"] == 400

    # Start using previewed points
    connect(client)
    res_start = client.post(
        "/api/routes/random",
        json={
            "center": {"latitude": 13.7563, "longitude": 100.5018},
            "radius_m": 400,
            "point_count": 6,
            "continuous": True,
            "initial_points": data["points"],
        },
    )
    assert res_start.status_code == 200
    started_data = res_start.json()
    assert started_data["points"] == data["points"]


def connect(client):
    client.post("/api/devices/connect", json={"udid": "mock-iphone"})
