from pydantic import BaseModel, Field


class UsageSummaryOut(BaseModel):
    """Dashboard usage KPI summary for a tenant/meter."""

    kWhThisMonth: float = Field(..., description="kWh total for current month window.")
    kWhLastMonth: float = Field(..., description="kWh total for previous month window.")
    costThisMonth: float = Field(..., description="Estimated cost for current month.")
    anomalyScore: float = Field(..., description="Latest anomaly score (0 if unavailable).")


class BenchmarkingOut(BaseModel):
    """Benchmarking metrics aligned with frontend stub."""

    percentile: float = Field(..., ge=0, le=100, description="Percentile of your usage vs peers (0-100).")
    peerMedianKwh: float = Field(..., description="Peer median daily kWh (or kWh/day).")
    yourKwh: float = Field(..., description="Your average daily kWh (or kWh/day).")
