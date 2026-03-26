from __future__ import annotations

from typing import Any, Dict

import httpx
from fastapi import HTTPException

from src.api.core.config import get_settings


class AlertingClient:
    """HTTP client for alerting_service internal API."""

    def __init__(self) -> None:
        self._client = httpx.AsyncClient(timeout=30.0)

    async def close(self) -> None:
        """Close underlying HTTP client."""
        await self._client.aclose()

    def _base(self) -> str:
        settings = get_settings()
        if not settings.alerting_service_url:
            raise HTTPException(status_code=500, detail="ALERTING_SERVICE_URL is not configured")
        return settings.alerting_service_url.rstrip("/")

    async def trigger(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Trigger (persist) an alert."""
        url = self._base() + "/internal/alerts/trigger"
        resp = await self._client.post(url, json=payload)
        if resp.status_code >= 400:
            raise HTTPException(status_code=502, detail={"error": "alerting_service_failed", "body": resp.text})
        return resp.json()

    async def list_alerts(self, params: Dict[str, Any]) -> list[Dict[str, Any]]:
        """List alerts for a tenant (optionally filtered)."""
        url = self._base() + "/internal/alerts"
        resp = await self._client.get(url, params=params)
        if resp.status_code >= 400:
            raise HTTPException(status_code=502, detail={"error": "alerting_service_failed", "body": resp.text})
        data = resp.json()
        if not isinstance(data, list):
            raise HTTPException(status_code=502, detail={"error": "alerting_service_bad_response", "body": data})
        return data

    async def ack_alert(self, alert_id: str) -> Dict[str, Any]:
        """Acknowledge an alert."""
        url = self._base() + f"/internal/alerts/{alert_id}/ack"
        resp = await self._client.post(url)
        if resp.status_code >= 400:
            raise HTTPException(status_code=502, detail={"error": "alerting_service_failed", "body": resp.text})
        return resp.json()
