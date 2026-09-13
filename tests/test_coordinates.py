import math

import pytest
from pydantic import ValidationError

from models.schemas import Coordinates, Movement, Speed
from services.movement_engine import bearing, destination, distance


@pytest.mark.parametrize("lat,lon", [(91, 0), (-91, 0), (0, 181), (0, -181), (math.nan, 0), (0, math.inf)])
def test_invalid_coordinates(lat, lon):
    with pytest.raises(ValidationError):
        Coordinates(latitude=lat, longitude=lon)


@pytest.mark.parametrize("lat,lon", [(90, 180), (-90, -180), (0, 0)])
def test_coordinate_bounds(lat, lon):
    assert Coordinates(latitude=lat, longitude=lon).latitude == lat


def test_walking_one_second():
    start = Coordinates(latitude=13.7563, longitude=100.5018)
    end = destination(start, 0, 5 / 3.6)
    assert distance(start, end) == pytest.approx(1.388888889, abs=1e-6)
    assert end.latitude > start.latitude
    assert end.longitude == pytest.approx(start.longitude)


@pytest.mark.parametrize("heading", [0, 45, 90, 180, 270, 359])
@pytest.mark.parametrize("latitude", [-80, 0, 80])
def test_bearing_and_distance(latitude, heading):
    start = Coordinates(latitude=latitude, longitude=179.999)
    end = destination(start, heading, 1000)
    assert distance(start, end) == pytest.approx(1000, abs=1e-6)
    assert bearing(start, end) == pytest.approx(heading, abs=1e-6)
    assert -180 <= end.longitude <= 180


def test_invalid_websocket_values():
    for speed in [0, 200.1, float("nan")]:
        with pytest.raises(ValidationError):
            Speed(type="speed", kmh=speed)
    with pytest.raises(ValidationError):
        Movement(type="movement", active=True, bearing=float("inf"))
