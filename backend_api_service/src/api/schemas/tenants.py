from pydantic import BaseModel, Field


class TenantCreate(BaseModel):
    """Tenant creation payload."""
    name: str = Field(..., min_length=1, max_length=200, description="Tenant display name.")
    slug: str = Field(..., min_length=2, max_length=80, pattern=r"^[a-z0-9-]+$", description="URL-safe tenant slug.")


class TenantOut(BaseModel):
    """Tenant response."""
    id: str = Field(..., description="Tenant UUID.")
    name: str
    slug: str
    status: str
