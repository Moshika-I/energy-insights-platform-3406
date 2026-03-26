from pydantic import BaseModel, Field


class Pagination(BaseModel):
    """Pagination request parameters."""
    limit: int = Field(default=50, ge=1, le=500, description="Max number of items to return.")
    offset: int = Field(default=0, ge=0, description="Number of items to skip.")
