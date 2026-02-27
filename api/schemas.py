"""Pydantic schemas for the API."""
from datetime import datetime
from pydantic import BaseModel


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


class RangePredictionItem(BaseModel):
    hour: int
    predicted_at: datetime
    predicted_occupancy_pct: float


class RangePredictionResponse(BaseModel):
    pool_uid: str
    pool_name: str
    date: str
    predictions: list[RangePredictionItem]
