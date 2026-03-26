from pydantic import BaseModel, Field


class AlertTrigger(BaseModel):
    """Trigger an alert based on an analytics output."""
    tenant_id: str = Field(..., description="Tenant UUID.")
    user_id: str | None = Field(default=None, description="Optional user UUID to notify.")
    meter_id: str | None = Field(default=None, description="Optional meter UUID.")
    analytics_output_id: str | None = Field(default=None, description="Optional analytics output UUID.")
    severity: str = Field(default="warning", description="info|warning|critical")
    title: str = Field(..., min_length=1, max_length=200, description="Alert title.")
    message: str = Field(..., min_length=1, max_length=2000, description="Alert message.")
    channel: str = Field(default="in_app", description="in_app|email|sms|webhook")


class AlertOut(BaseModel):
    """Alert response."""
    id: str
    tenant_id: str
    user_id: str | None = None
    meter_id: str | None = None
    severity: str
    title: str
    message: str
    status: str
    channel: str
