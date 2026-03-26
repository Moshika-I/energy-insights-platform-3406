from __future__ import annotations

import argparse
import json
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional

import httpx


@dataclass(frozen=True)
class SmokeConfig:
    """Runtime configuration for the smoke-check client."""

    backend_base_url: str
    api_key: str
    artifact_dir: Path
    timeout_s: float = 30.0


def _write_artifact(artifact_dir: Path, name: str, content: Any) -> None:
    artifact_dir.mkdir(parents=True, exist_ok=True)
    p = artifact_dir / name
    if isinstance(content, (dict, list)):
        p.write_text(json.dumps(content, indent=2, default=str))
    else:
        p.write_text(str(content))


def _write_http_artifacts(
    artifact_dir: Path, step: str, resp: httpx.Response, extra: Optional[Dict[str, Any]] = None
) -> None:
    """Persist response details for debugging with minimal stdout."""
    base = {
        "status_code": resp.status_code,
        "headers": dict(resp.headers),
        "text": resp.text,
    }
    try:
        base["json"] = resp.json()
    except Exception:
        base["json"] = None

    if extra:
        base["extra"] = extra

    _write_artifact(artifact_dir, f"{step}.json", base)


def _require_env(name: str) -> str:
    v = os.getenv(name, "")
    if not v:
        raise RuntimeError(f"Missing required env var: {name}")
    return v


def _iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


# PUBLIC_INTERFACE
def main(argv: Optional[list[str]] = None) -> int:
    """Run an end-to-end smoke check against backend_api_service.

    Validates:
      - backend health
      - tenant + meter creation (backend↔db)
      - readings ingestion (backend↔db)
      - analytics orchestration call (backend↔analytics_service) + persistence (backend↔db)
      - alert listing (backend↔alerting_service) [alert may or may not exist depending on score]

    Artifacts:
      - Writes per-step HTTP responses to --artifact-dir (default: /tmp/smoke-check)

    Args:
        argv: Optional argv override.

    Returns:
        Process exit code (0=pass, 1=fail).
    """
    parser = argparse.ArgumentParser(prog="smoke-check", add_help=True)
    parser.add_argument("--artifact-dir", default="/tmp/smoke-check", help="Directory for response artifacts.")
    args = parser.parse_args(argv)

    try:
        cfg = SmokeConfig(
            backend_base_url=os.getenv("BACKEND_BASE_URL", "http://127.0.0.1:8000").rstrip("/"),
            api_key=_require_env("BACKEND_API_KEY"),
            artifact_dir=Path(args.artifact_dir),
        )
    except Exception as e:
        Path(args.artifact_dir).mkdir(parents=True, exist_ok=True)
        _write_artifact(Path(args.artifact_dir), "error.txt", f"config_error: {e}")
        return 1

    headers = {"X-API-Key": cfg.api_key}

    tenant_slug = f"smoke-{uuid.uuid4().hex[:10]}"
    meter_name = f"Smoke Meter {uuid.uuid4().hex[:6]}"

    now = datetime.now(timezone.utc)
    window_start = now - timedelta(days=10)
    window_end = now

    # Make last reading an outlier to encourage should_alert but not guaranteed (depends on stats).
    readings = []
    for i in range(9):
        readings.append({"reading_at": _iso(window_start + timedelta(days=i)), "value": 10.0, "quality": "actual", "source": "smoke"})
    readings.append({"reading_at": _iso(window_start + timedelta(days=9)), "value": 200.0, "quality": "actual", "source": "smoke"})

    try:
        with httpx.Client(timeout=cfg.timeout_s) as client:
            # 00 health
            r = client.get(f"{cfg.backend_base_url}/")
            _write_http_artifacts(cfg.artifact_dir, "00_backend_health", r)
            r.raise_for_status()

            # 01 create tenant
            r = client.post(
                f"{cfg.backend_base_url}/auth/tenants",
                headers=headers,
                json={"name": "Smoke Tenant", "slug": tenant_slug},
            )
            _write_http_artifacts(cfg.artifact_dir, "01_create_tenant", r)
            r.raise_for_status()
            tenant_id = str(r.json()["id"])

            # 02 create meter
            r = client.post(
                f"{cfg.backend_base_url}/ingestion/meters",
                headers=headers,
                json={
                    "tenant_id": tenant_id,
                    "name": meter_name,
                    "meter_type": "electric",
                    "unit": "kwh",
                    "timezone": "UTC",
                    "external_id": None,
                    "location": "Smoke Location",
                },
            )
            _write_http_artifacts(cfg.artifact_dir, "02_create_meter", r)
            r.raise_for_status()
            meter_id = str(r.json()["id"])

            # 03 ingest readings
            r = client.post(
                f"{cfg.backend_base_url}/ingestion/readings",
                headers=headers,
                json={"tenant_id": tenant_id, "meter_id": meter_id, "readings": readings},
            )
            _write_http_artifacts(cfg.artifact_dir, "03_ingest_readings", r)
            r.raise_for_status()

            # 04 run analytics orchestration (calls analytics_service, persists output, may trigger alert)
            r = client.post(
                f"{cfg.backend_base_url}/orchestration/analyze",
                headers=headers,
                json={
                    "tenant_id": tenant_id,
                    "meter_id": meter_id,
                    "window_start": _iso(window_start),
                    "window_end": _iso(window_end),
                    "granularity": "daily",
                },
            )
            _write_http_artifacts(cfg.artifact_dir, "04_run_analytics", r)
            r.raise_for_status()
            analytics_output_id = str(r.json().get("id", ""))

            # 05 ui meters list
            r = client.get(
                f"{cfg.backend_base_url}/ui/meters",
                headers=headers,
                params={"tenant_id": tenant_id, "limit": 50, "offset": 0},
            )
            _write_http_artifacts(cfg.artifact_dir, "05_ui_list_meters", r)
            r.raise_for_status()

            # 06 ui usage summary
            r = client.get(
                f"{cfg.backend_base_url}/ui/analytics/usage-summary",
                headers=headers,
                params={"tenant_id": tenant_id, "meter_id": meter_id},
            )
            _write_http_artifacts(cfg.artifact_dir, "06_ui_usage_summary", r)
            r.raise_for_status()

            # 07 ui benchmarking
            r = client.get(
                f"{cfg.backend_base_url}/ui/analytics/benchmarking",
                headers=headers,
                params={"tenant_id": tenant_id, "meter_id": meter_id, "window_days": 30},
            )
            _write_http_artifacts(cfg.artifact_dir, "07_ui_benchmarking", r)
            r.raise_for_status()

            # 08 ui list alerts (may be empty; still validates backend↔alerting_service HTTP path)
            r = client.get(
                f"{cfg.backend_base_url}/ui/alerts",
                headers=headers,
                params={"tenant_id": tenant_id, "limit": 50, "offset": 0},
            )
            _write_http_artifacts(
                cfg.artifact_dir,
                "08_ui_list_alerts",
                r,
                extra={"analytics_output_id": analytics_output_id, "tenant_id": tenant_id, "meter_id": meter_id},
            )
            r.raise_for_status()

            # success summary artifact
            _write_artifact(
                cfg.artifact_dir,
                "summary.json",
                {
                    "backend_base_url": cfg.backend_base_url,
                    "tenant_id": tenant_id,
                    "meter_id": meter_id,
                    "analytics_output_id": analytics_output_id,
                    "tenant_slug": tenant_slug,
                },
            )

        return 0
    except Exception as e:
        _write_artifact(cfg.artifact_dir, "error.txt", f"runtime_error: {e}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
