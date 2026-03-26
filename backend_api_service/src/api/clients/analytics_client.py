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
        await self._client.aclose()

    async def analyze(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        settings = get_settings()
        if not settings.analytics_service_url:
            raise HTTPException(status_code=500, detail="ANALYTICS_SERVICE_URL is not configured")

        url = settings.analytics_service_url.rstrip("/") + "/internal/analyze"
        resp = await self._client.post(url, json=payload)
        if resp.status_code >= 400:
            raise HTTPException(status_code=502, detail={"error": "analytics_service_failed", "body": resp.text})
        return resp.json()
