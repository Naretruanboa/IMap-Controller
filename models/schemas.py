from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Coordinates(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False, extra="forbid")
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)


class Favorite(Coordinates):
    name: str = Field(min_length=1, max_length=120)


class RouteRequest(BaseModel):
    points: list[Coordinates] = Field(min_length=2, max_length=10000)
    loops: int = Field(default=1, ge=0, le=1000)  # 0 = infinite


class Movement(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    type: Literal["movement"]
    active: bool
    bearing: float = Field(default=0, ge=0, lt=360)


class Speed(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    type: Literal["speed"]
    kmh: float = Field(ge=0.1, le=200)
    schedule: Literal["off", "target10k"] = "off"


class DeviceSelection(BaseModel):
    udid: str = Field(min_length=1, max_length=128)


class GPXRequest(BaseModel):
    xml: str = Field(max_length=2_000_000)
