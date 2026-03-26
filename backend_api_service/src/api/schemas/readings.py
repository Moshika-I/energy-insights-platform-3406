from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field


class ReadingIn(BaseModel):
    """Single meter reading."""
    reading_at: datetime = Field(..., description="Timestamp of the reading (ISO8601).")
    value: Decimal = Field(..., description="Consumption/usage value.")
    quality: str = Field(default="actual", description="Quality flag.")
    source: str = Field(default="upload", description="Source of reading.")


class ReadingsIngest(BaseModel):
    """Batch readings ingest payload."""
    tenant_id: str = Field(..., description="Tenant UUID.")
    meter_id: str = Field(..., description="Meter UUID.")
    readings: list[ReadingIn] = Field(..., min_length=1, max_length=5000, description="Readings batch.")
