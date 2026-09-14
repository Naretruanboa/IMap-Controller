import math
import random

from models.schemas import Coordinates
from services.movement_engine import bearing, destination, distance


def generate_random_point_in_radius(center: Coordinates, radius_m: float) -> Coordinates:
    r = radius_m * math.sqrt(random.random())
    r = max(5.0, r) if radius_m >= 10 else r
    theta = random.uniform(0, 360)
    return destination(center, theta, r)


def generate_random_points(
    center: Coordinates,
    radius_m: float,
    count: int = 5,
    start: Coordinates | None = None,
) -> list[Coordinates]:
    if count < 2:
        raise ValueError("Random route must contain at least two waypoints")
    points: list[Coordinates] = []
    if start is not None:
        points.append(start)
    else:
        points.append(generate_random_point_in_radius(center, radius_m))

    for _ in range(len(points), count):
        cand = generate_random_point_in_radius(center, radius_m)
        for _attempt in range(10):
            cand = generate_random_point_in_radius(center, radius_m)
            if distance(points[-1], cand) >= min(10.0, radius_m * 0.1):
                break
        points.append(cand)
    return points


class RouteEngine:
    """Repeated routes return along a geodesic closing segment; never jump to the start."""

    def __init__(self, points: list[Coordinates], loops: int = 1):
        if len(points) < 2 or sum(distance(a, b) for a, b in zip(points, points[1:])) < 0.01:
            raise ValueError("Route must contain at least two distinct points")
        self.points = points
        self.loops = loops
        self.current_segment = 0
        self.completed_loops = 0
        self.status = "running"
        self.travelled = 0.0
        self.base_distance = sum(distance(a, b) for a, b in zip(points, points[1:]))
        self.closing_distance = distance(points[-1], points[0])

    def advance(self, position: Coordinates, meters: float) -> tuple[Coordinates, float]:
        heading = 0.0
        if self.status != "running":
            return position, heading
        while meters > 0 and self.status == "running":
            target = self.points[(self.current_segment + 1) % len(self.points)]
            remaining = distance(position, target)
            heading = bearing(position, target)
            step = min(meters, remaining)
            self.travelled += step
            meters -= step
            if remaining > step + 1e-8:
                return destination(position, heading, step), heading
            position = target
            self.current_segment += 1
            if self.current_segment == len(self.points) - 1:
                self.completed_loops += 1
                if self.loops and self.completed_loops >= self.loops:
                    self.status = "completed"
            elif self.current_segment == len(self.points):
                self.current_segment = 0
        return position, heading

    def snapshot(self) -> dict:
        total = self.base_distance * self.loops + self.closing_distance * max(0, self.loops - 1)
        return {
            "type": "route_state",
            "route_type": "standard",
            "status": self.status,
            "current_segment": self.current_segment,
            "completed_loops": self.completed_loops,
            "loops": self.loops,
            "points": [{"latitude": p.latitude, "longitude": p.longitude} for p in self.points],
            "route_progress": (1.0 if self.status == "completed" else min(1, self.travelled / total))
            if total
            else None,
            "distance_remaining": (0.0 if self.status == "completed" else max(0, total - self.travelled))
            if self.loops
            else None,
        }


class RandomRouteEngine(RouteEngine):
    """Walks random routes within a specified radius, automatically regenerating new routes upon completion."""

    def __init__(
        self,
        center: Coordinates,
        radius_m: float = 500.0,
        point_count: int = 5,
        continuous: bool = True,
        start_position: Coordinates | None = None,
        initial_points: list[Coordinates] | None = None,
    ):
        if radius_m < 10:
            raise ValueError("Radius must be at least 10 meters")
        if point_count < 2:
            raise ValueError("Point count must be at least 2")
        self.center = center
        self.radius_m = radius_m
        self.point_count = point_count
        self.continuous = continuous
        self.cycle_count = 1
        if initial_points and len(initial_points) >= 2:
            points = initial_points
        else:
            points = generate_random_points(center, radius_m, point_count, start=start_position)
        super().__init__(points, loops=0 if continuous else 1)

    def _next_cycle(self, current_position: Coordinates) -> None:
        self.cycle_count += 1
        self.points = generate_random_points(
            self.center, self.radius_m, self.point_count, start=current_position
        )
        self.current_segment = 0
        self.base_distance = sum(distance(a, b) for a, b in zip(self.points, self.points[1:]))
        self.closing_distance = 0.0

    def reroll(self, current_position: Coordinates) -> None:
        self._next_cycle(current_position)

    def advance(self, position: Coordinates, meters: float) -> tuple[Coordinates, float]:
        heading = 0.0
        if self.status != "running":
            return position, heading
        while meters > 0 and self.status == "running":
            target = self.points[self.current_segment + 1]
            remaining = distance(position, target)
            heading = bearing(position, target)
            step = min(meters, remaining)
            self.travelled += step
            meters -= step
            if remaining > step + 1e-8:
                return destination(position, heading, step), heading
            position = target
            self.current_segment += 1
            if self.current_segment >= len(self.points) - 1:
                self.completed_loops += 1
                if self.continuous:
                    self._next_cycle(position)
                else:
                    self.status = "completed"
        return position, heading

    def snapshot(self) -> dict:
        cycle_total = sum(distance(a, b) for a, b in zip(self.points, self.points[1:]))
        return {
            "type": "route_state",
            "route_type": "random",
            "status": self.status,
            "center": {"latitude": self.center.latitude, "longitude": self.center.longitude},
            "radius_m": self.radius_m,
            "point_count": self.point_count,
            "continuous": self.continuous,
            "cycle": self.cycle_count,
            "points": [{"latitude": p.latitude, "longitude": p.longitude} for p in self.points],
            "current_segment": self.current_segment,
            "completed_loops": self.completed_loops,
            "loops": 0 if self.continuous else 1,
            "total_travelled": self.travelled,
            "cycle_total_distance": cycle_total,
            "route_progress": None if self.continuous else (1.0 if self.status == "completed" else min(1, self.travelled / (cycle_total or 1))),
            "distance_remaining": None,
        }
