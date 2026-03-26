from pydantic import BaseModel, EmailStr, Field


class UserCreate(BaseModel):
    """Create or upsert a user for a tenant (lightweight template auth)."""
    tenant_id: str = Field(..., description="Tenant UUID.")
    email: EmailStr = Field(..., description="User email.")
    full_name: str | None = Field(default=None, max_length=200, description="Full name.")
    auth_subject: str | None = Field(default=None, max_length=255, description="External auth subject identifier.")


class UserOut(BaseModel):
    """User response."""
    id: str = Field(..., description="User UUID.")
    tenant_id: str = Field(..., description="Tenant UUID.")
    email: EmailStr
    full_name: str | None = None
    status: str
