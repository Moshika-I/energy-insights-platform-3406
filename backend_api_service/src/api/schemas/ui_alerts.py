from datetime import datetime

from pydantic import BaseModel, Field


class UiAlertOut(BaseModel):
    """UI-friendly alert model aligned to frontend typed stub."""

    id: str = Field(..., description="Alert UUID.")
    severity: str = Field(..., description="low|medium|high")
    title: str
    description: str
    createdAt: datetime
    acknowledged: bool
