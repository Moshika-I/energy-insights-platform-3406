from datetime import datetime

from pydantic import BaseModel, Field


class DocumentOut(BaseModel):
    """Document list response."""

    id: str = Field(..., description="Document UUID.")
    name: str = Field(..., description="Original filename.")
    tags: list[str] = Field(default_factory=list, description="Document tags.")
    uploaded_at: datetime = Field(..., description="Upload timestamp.")
    status: str = Field(..., description="uploaded|processing|processed|failed|deleted")
