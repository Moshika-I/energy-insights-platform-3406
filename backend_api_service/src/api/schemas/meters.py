from pydantic import BaseModel, Field


class MeterCreate(BaseModel):
    """Create a meter under a tenant."""
    tenant_id: str = Field(..., description="Tenant UUID.")
    name: str = Field(..., min_length=1, max_length=200, description="Meter name.")
    meter_type: str = Field(default="electric", description="Meter type.")
    unit: str = Field(default="kwh", description="Unit of measure.")
    timezone: str = Field(default="UTC", description="IANA timezone.")
    external_id: str | None = Field(default=None, max_length=200, description="External meter identifier.")
    location: str | None = Field(default=None, max_length=255, description="Location label.")


class MeterOut(BaseModel):
    """Meter response."""
    id: str
    tenant_id: str
    name: str
    meter_type: str
    unit: str
    timezone: str
    external_id: str | None = None
    location: str | None = None
