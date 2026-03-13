"""Pydantic schemas for the API."""
from datetime import datetime
from typing import Annotated
from pydantic import BaseModel, field_validator


class HealthResponse(BaseModel):
    status: str
    version: str


class PoolInfo(BaseModel):
    uid: str
    name: str
    type: str
    seasonal: bool
    city: str
    max_capacity: int


class PredictionResponse(BaseModel):
    pool_uid: str
    pool_name: str
    predicted_at: datetime
    predicted_occupancy_pct: float
    model_version: str

    @field_validator("predicted_occupancy_pct")
    @classmethod
    def round_pct(cls, v: float) -> float:
        return round(v, 1)


class RangePredictionItem(BaseModel):
    hour: int
    predicted_at: datetime
    predicted_occupancy_pct: float

    @field_validator("predicted_occupancy_pct")
    @classmethod
    def round_pct(cls, v: float) -> float:
        return round(v, 1)


class RangePredictionResponse(BaseModel):
    pool_uid: str
    pool_name: str
    date: str
    predictions: list[RangePredictionItem]
