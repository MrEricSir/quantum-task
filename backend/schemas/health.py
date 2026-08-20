from pydantic import BaseModel


class HealthMeasurementCreate(BaseModel):
    date: str      # YYYY-MM-DD
    metric: str    # one of routers.health.MANUAL_METRICS
    value: float
