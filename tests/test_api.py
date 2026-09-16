import time

import pytest
from fastapi.testclient import TestClient

from app import create_app

POINT = {"latitude": 13.7563, "longitude": 100.5018}


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app("mock", str(tmp_path / "test.db"))) as client:
        yield client


def connect(client):
    assert client.post("/api/devices/connect", json={"udid": "mock-iphone"}).status_code == 200


def receive_type(ws, kind, condition=lambda x: True):
    for _ in range(100):
        message = ws.receive_json()
        if message["type"] == kind and condition(message):
            return message
    raise AssertionError(f"Missing {kind}")


def test_static_and_discovery(client):
    for path in [
        "/",
        "/static/js/app.js",
        "/static/js/map.js",
        "/static/css/app.css",
        "/static/vendor/leaflet.js",
    ]:
        assert client.get(path).status_code == 200
    assert client.get("/api/devices").json()[0]["connection"] == "MOCK"
    assert client.get("/api/state").json()["device"]["connected"] is False
    ver = client.get("/api/version").json()
    assert ver["version"] == "2.6.0"
    assert "features" in ver


def test_place_search_endpoint(client):
    async def search(query, language):
        return [{"name": query, "latitude": 13.75, "longitude": 100.5, "type": "city"}]

    client.app.state.controller.geocoder.search = search
    response = client.get("/api/search", params={"q": "Bangkok", "language": "th"})
    assert response.status_code == 200
    assert response.json()["results"][0]["name"] == "Bangkok"
    assert client.get("/api/search", params={"q": "x"}).status_code == 422


def test_teleport_restore_validation(client):
    assert client.post("/api/location/set", json=POINT).status_code == 503
    connect(client)
    assert client.post("/api/location/set", json={"latitude": 91, "longitude": 0}).status_code == 422
    assert client.post("/api/location/set", json=POINT).json()["simulation_active"]
    assert len(client.get("/api/history").json()) == 1
    assert client.post("/api/location/clear").json()["simulation_active"] is False
    assert client.post("/api/devices/disconnect").status_code == 200


def test_favorites_and_persistence(tmp_path):
    path = str(tmp_path / "persist.db")
    with TestClient(create_app("mock", path)) as client:
        connect(client)
        client.post("/api/location/set", json=POINT)
        assert client.post("/api/favorites", json={**POINT, "name": "Home"}).status_code == 201
    with TestClient(create_app("mock", path)) as client:
        items = client.get("/api/favorites").json()
        assert items[0]["name"] == "Home"
        assert len(client.get("/api/history").json()) == 1
        assert client.delete(f"/api/favorites/{items[0]['id']}").status_code == 200
        assert client.get("/api/favorites").json() == []


def test_routes_and_gpx(client):
    connect(client)
    body = {"points": [POINT, {"latitude": 13.757, "longitude": 100.502}], "loops": 2}
    assert client.post("/api/routes/start", json=body).json()["status"] == "running"
    assert client.post("/api/routes/pause").json()["status"] == "paused"
    assert client.post("/api/routes/resume").json()["status"] == "running"
    assert client.post("/api/routes/stop").json()["status"] == "stopped"
    assert client.post("/api/routes/resume").status_code == 422
    assert client.post("/api/gpx/import", json={"xml": "<gpx/>"}).status_code == 422
    assert client.post("/api/routes/start", json={"points": [POINT, POINT]}).status_code == 422


@pytest.mark.parametrize("count", [2, 3])
def test_route_actually_moves_pauses_and_finishes(client, count):
    connect(client)
    points = [{**POINT, "latitude": POINT["latitude"] + i * 0.00001} for i in range(count)]
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "speed", "kmh": 5})
        assert client.post("/api/routes/start", json={"points": points}).status_code == 200
        receive_type(ws, "location_state", lambda m: m["latitude"] is not None and m["latitude"] > POINT["latitude"])
        assert client.post("/api/routes/pause").json()["status"] == "paused"
        paused = client.get("/api/state").json()["location"]
        time.sleep(0.25)
        assert client.get("/api/state").json()["location"] == paused
        assert client.post("/api/routes/resume").status_code == 200
        receive_type(ws, "route_state", lambda m: m["status"] == "completed")
        location = client.get("/api/state").json()["location"]
        assert location["latitude"] == points[-1]["latitude"]
        assert not location["moving"]
        provider = client.app.state.controller.state.provider
        assert provider.location == (points[-1]["latitude"], points[-1]["longitude"])


def test_websocket_movement_speed_and_deadman(client):
    connect(client)
    client.post("/api/location/set", json=POINT)
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "speed", "kmh": 200})
        assert receive_type(ws, "location_state", lambda m: m["speed_kmh"] == 200)
        ws.send_json({"type": "movement", "bearing": 0, "active": True})
        changed = receive_type(ws, "location_state", lambda m: m["latitude"] > POINT["latitude"])
        assert changed["moving"]
        stopped = receive_type(ws, "location_state", lambda m: not m["moving"])
        assert stopped["latitude"] > POINT["latitude"]
        ws.send_text("not json")
        assert receive_type(ws, "error")["code"] == "INVALID_COMMAND"
        ws.send_json({"type": "speed", "kmh": 999})
        assert receive_type(ws, "error")


def test_disconnect_stops_owned_motion(client):
    connect(client)
    client.post("/api/location/set", json=POINT)
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "movement", "bearing": 45, "active": True})
        receive_type(ws, "location_state", lambda m: m["moving"])
    time.sleep(0.05)
    assert client.get("/api/state").json()["location"]["moving"] is False


def test_origin_and_host_protection(client):
    connect(client)
    assert (
        client.post("/api/location/set", json=POINT, headers={"Origin": "https://evil.example"}).status_code
        == 403
    )
    assert client.get("/api/state", headers={"Host": "evil.example"}).status_code == 400
    with pytest.raises(Exception):
        with client.websocket_connect("/ws", headers={"Origin": "https://evil.example"}):
            pass


def test_shutdown_clears_simulation(tmp_path):
    app = create_app("mock", str(tmp_path / "shutdown.db"))
    with TestClient(app) as client:
        connect(client)
        client.post("/api/location/set", json=POINT)
        provider = app.state.controller.state.provider
        assert provider.location
    assert provider.location is None
    assert not provider.connected


def test_target_speed_schedule_and_manual_override(client):
    connect(client)
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "speed", "kmh": 5, "schedule": "target10k"})
        state = receive_type(ws, "location_state", lambda s: s.get("speed_schedule") == "target10k")
        assert state["speed_kmh"] == 5
        assert state["speed_schedule_elapsed"] == 0
        ws.send_json({"type": "speed", "kmh": 5})
        state = receive_type(ws, "location_state", lambda s: s["speed_kmh"] == 5)
        assert state["speed_schedule"] == "off"


def test_distance_counter_excludes_teleports_and_resets_independently(client):
    connect(client)
    c = client.app.state.controller
    assert client.get("/api/state").json()["location"]["distance_m"] == 0
    c.state.distance_m = 1234.5
    c.state.speed_schedule = "target10k"
    c.state.speed_schedule_elapsed = 42
    response = client.post("/api/location/set", json=POINT)
    assert response.json()["distance_m"] == 1234.5
    response = client.post("/api/location/clear")
    assert response.json()["distance_m"] == 1234.5
    c.state.speed_schedule = "target10k"
    c.state.speed_schedule_elapsed = 42
    response = client.post("/api/movement/distance/reset")
    assert response.status_code == 200
    assert response.json()["distance_m"] == 0
    assert response.json()["speed_schedule"] == "target10k"
    assert response.json()["speed_schedule_elapsed"] == 42


def test_run_requires_start_and_retargets_without_teleport(client):
    from models.schemas import Coordinates
    from services.movement_engine import distance

    target = {**POINT, "latitude": POINT["latitude"] + 0.01}
    assert client.post("/api/location/run", json=target).status_code == 503
    connect(client)
    assert client.post("/api/location/run", json=target).status_code == 422
    client.post("/api/location/set", json=POINT)
    state = client.app.state.controller.state
    state.speed_kmh = 200
    assert client.post("/api/location/run", json=target).json()["status"] == "running"
    assert state.route.points[0] == Coordinates(**POINT)
    assert distance(Coordinates(**POINT), state.position) < 30
    assert state.speed_kmh == 200
    assert client.post("/api/routes/pause").json()["status"] == "paused"
    paused = state.position
    replacement = {**POINT, "longitude": POINT["longitude"] + 0.01}
    assert client.post("/api/location/run", json=replacement).status_code == 200
    assert state.route.points == [paused, Coordinates(**replacement)]
    assert distance(paused, state.position) < 30
    assert client.post("/api/routes/stop").json()["status"] == "stopped"


def test_run_moves_at_selected_speed_and_arrives(client):
    from models.schemas import Coordinates
    from services.movement_engine import distance

    connect(client)
    client.post("/api/location/set", json=POINT)
    target = {**POINT, "latitude": POINT["latitude"] + 0.00001}
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "speed", "kmh": 5})
        assert client.post("/api/location/run", json=target).status_code == 200
        state = receive_type(ws, "location_state", lambda m: POINT["latitude"] < (m["latitude"] or 0) < target["latitude"])
        assert state["speed_kmh"] == 5
        receive_type(ws, "route_state", lambda m: m["status"] == "completed")
        position = client.app.state.controller.state.position
        assert distance(position, Coordinates(**target)) == pytest.approx(0)
