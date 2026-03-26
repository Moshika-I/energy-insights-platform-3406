from datetime import datetime
from pydantic import BaseModel, Field


class AnalyticsRequest(BaseModel):
    """Request analytics for a meter and time window."""
    tenant_id: str = Field(..., description="Tenant UUID.")
    meter_id: str = Field(..., description="Meter UUID.")
    window_start: datetime = Field(..., description="Start time.")
    window_end: datetime = Field(..., description="End time.")
    granularity: str = Field(default="daily", description="Aggregation granularity (daily/weekly/monthly).")


class AnalyticsOutputOut(BaseModel):
    """Stored analytics output reference."""
    id: str = Field(..., description="analytics_outputs UUID.")
    output_type: str
    granularity: str | None = None
    window_start: datetime | None = None
    window_end: datetime | None = None
    score: float | None = None
    output: dict = Field(..., description="Output JSON payload.")
