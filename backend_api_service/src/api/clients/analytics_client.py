from __future__ import annotations

from typing import Any, Dict

import httpx
from fastapi import HTTPException

from src.api.core.config import get_settings


class AnalyticsClient:
    """HTTP client for analytics_service internal API."""

    def __init__(self) -> None:
        self._client = httpx.AsyncClient(timeout=30.0)

    async def close(self) -> None:
        """Close underlying HTTP client."""
        await self._client.aclose()

    def _base(self) -> str:
        settings = get_settings()
        if not settings.analytics_service_url:
            raise HTTPException(status_code=500, detail="ANALYTICS_SERVICE_URL is not configured")
        return settings.analytics_service_url.rstrip("/")

    async def analyze(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Analyze a readings window (orchestration)."""
        url = self._base() + "/internal/analyze"
        resp = await self._client.post(url, json=payload)
        if resp.status_code >= 400:
            raise HTTPException(status_code=502, detail={"error": "analytics_service_failed", "body": resp.text})
        return resp.json()

    async def benchmark(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Get benchmark for a meter vs tenant peers."""
        url = self._base() + "/internal/benchmark"
        resp = await self._client.get(url, params=params)
        if resp.status_code >= 400:
            raise HTTPException(status_code=502, detail={"error": "analytics_service_failed", "body": resp.text})
        data = resp.json()
        if not isinstance(data, dict):
            raise HTTPException(status_code=502, detail={"error": "analytics_service_bad_response", "body": data})
        return data

    async def anomalies(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Compute anomaly score from DB readings."""
        url = self._base() + "/internal/anomalies"
        resp = await self._client.get(url, params=params)
        if resp.status_code >= 400:
            raise HTTPException(status_code=502, detail={"error": "analytics_service_failed", "body": resp.text})
        data = resp.json()
        if not isinstance(data, dict):
            raise HTTPException(status_code=502, detail={"error": "analytics_service_bad_response", "body": data})
        return data
