from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import Depends, FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse

from src.api.clients.alerting_client import AlertingClient
from src.api.clients.analytics_client import AnalyticsClient
from src.api.core.config import get_settings
from src.api.core.db import get_request_db, lifespan
from src.api.core.security import require_api_key
from src.api.schemas.alerts import AlertOut, AlertTrigger
from src.api.schemas.analytics import AnalyticsOutputOut, AnalyticsRequest
from src.api.schemas.dashboard import BenchmarkingOut, UsageSummaryOut
from src.api.schemas.documents import DocumentOut
from src.api.schemas.meters import MeterCreate, MeterOut
from src.api.schemas.readings import ReadingsIngest
from src.api.schemas.tenants import TenantCreate, TenantOut
from src.api.schemas.ui_alerts import UiAlertOut
from src.api.schemas.users import UserCreate, UserOut

openapi_tags = [
    {"name": "health", "description": "Service health and diagnostics."},
    {"name": "auth", "description": "Template auth/user provisioning endpoints (API-key protected)."},
    {"name": "ingestion", "description": "Data ingestion endpoints for meters, readings, and documents."},
    {"name": "orchestration", "description": "Orchestration endpoints that call analytics and alerting services."},
    {"name": "ui", "description": "UI-facing endpoints aligned with frontend client stubs."},
]


app = FastAPI(
    title="Energy Insights Platform - Backend API",
    description=(
        "API gateway for the Energy Insights Platform.\n\n"
        "- Auth: template API-key protected endpoints for tenant/user provisioning.\n"
        "- Ingestion: meters, readings, documents.\n"
        "- Orchestration: calls analytics_service and alerting_service internal APIs.\n\n"
        "All protected endpoints require header `X-API-Key`."
    ),
    version="0.3.0",
    openapi_tags=openapi_tags,
    lifespan=lifespan,
)

settings = get_settings()

# Optional Host header validation (recommended in production behind a stable domain)
_allowed_hosts = [h.strip() for h in (settings.allowed_hosts or "").split(",") if h.strip()]
if _allowed_hosts:
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=_allowed_hosts)

# Env-based CORS configuration
_allow_origins = [o.strip() for o in (settings.cors_allow_origins or "").split(",") if o.strip()]
if not _allow_origins:
    _allow_origins = ["*"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allow_origins,
    allow_credentials=bool(settings.cors_allow_credentials),
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def security_headers_middleware(request: Request, call_next):  # type: ignore[no-untyped-def]
    """Add lightweight security headers to all responses.

    This is intentionally minimal (no CSP) to avoid breaking the Next.js UI while still
    providing sensible defaults.
    """
    response = await call_next(request)

    if settings.security_headers_enabled:
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("Cross-Origin-Resource-Policy", "same-site")
        # If behind TLS-terminating proxy, HSTS can be enabled there; keep off by default here.
    return response


@app.get("/", tags=["health"], summary="Health check", operation_id="health_check")
# PUBLIC_INTERFACE
def health_check() -> Dict[str, str]:
    """Health check endpoint.

    Returns:
        JSON with a simple status message.
    """
    return {"message": "Healthy"}


@app.get("/healthz", tags=["health"], summary="Health check (healthz)", operation_id="health_check_healthz")
# PUBLIC_INTERFACE
def health_check_healthz() -> Dict[str, str]:
    """Health check endpoint (compat alias).

    The preview/runtime environment may probe `/healthz` (configured via env var
    HEALTHCHECK_PATH / NEXT_PUBLIC_HEALTHCHECK_PATH). We expose this alias so the
    service is recognized as healthy without changing the canonical `/` endpoint.

    Returns:
        JSON with a simple status message.
    """
    return {"message": "Healthy"}


@app.get(
    "/docs/integration",
    tags=["health"],
    summary="Integration notes (env vars + end-to-end flow)",
    operation_id="integration_notes",
)
# PUBLIC_INTERFACE
def integration_notes() -> Dict[str, Any]:
    """Integration notes for environment-based configuration and the E2E flow.

    End-to-end flow:
      1) Create tenant -> POST /auth/tenants
      2) Create meter  -> POST /ingestion/meters
      3) Upload readings -> POST /ingestion/readings
      4) Run analytics -> POST /orchestration/analyze (calls analytics_service)
      5) If anomaly score high, backend triggers alert -> alerting_service
      6) UI reads:
         - meters: GET /ui/meters
         - KPIs:   GET /ui/analytics/usage-summary and /ui/analytics/benchmarking
         - alerts: GET /ui/alerts, POST /ui/alerts/{id}/ack

    Required env vars (backend_api_service):
      - POSTGRES_URL
      - BACKEND_API_KEY
      - ANALYTICS_SERVICE_URL
      - ALERTING_SERVICE_URL
      - BACKEND_CORS_ALLOW_ORIGINS (optional, default '*')
      - BACKEND_ALLOWED_HOSTS (optional)
      - BACKEND_SECURITY_HEADERS_ENABLED (optional, default true)

    Returns:
        Dictionary with the above notes for quick diagnostics.
    """
    s = get_settings()
    return {
        "required_env": [
            "POSTGRES_URL",
            "BACKEND_API_KEY",
            "ANALYTICS_SERVICE_URL",
            "ALERTING_SERVICE_URL",
        ],
        "optional_env": [
            "BACKEND_CORS_ALLOW_ORIGINS",
            "BACKEND_CORS_ALLOW_CREDENTIALS",
            "BACKEND_ALLOWED_HOSTS",
            "BACKEND_SECURITY_HEADERS_ENABLED",
        ],
        "configured": {
            "analytics_service_url_set": bool(s.analytics_service_url),
            "alerting_service_url_set": bool(s.alerting_service_url),
            "cors_allow_origins": s.cors_allow_origins,
            "allowed_hosts": s.allowed_hosts,
            "security_headers_enabled": bool(s.security_headers_enabled),
        },
        "flow": "upload readings -> /orchestration/analyze -> optional alert -> /ui/alerts",
    }


# -----------------------------
# Tenants / Users (template auth)
# -----------------------------


@app.post(
    "/auth/tenants",
    tags=["auth"],
    summary="Create tenant",
    operation_id="create_tenant",
    dependencies=[Depends(require_api_key)],
    response_model=TenantOut,
)
# PUBLIC_INTERFACE
async def create_tenant(payload: TenantCreate, db=Depends(get_request_db)) -> TenantOut:
    """Create a tenant.

    Args:
        payload: TenantCreate request body.

    Returns:
        Newly created tenant.
    """
    rows = await db.fetch_all(
        """
        INSERT INTO tenants (name, slug)
        VALUES (:name, :slug)
        RETURNING id::text AS id, name, slug, status
        """,
        {"name": payload.name, "slug": payload.slug},
    )
    return TenantOut(**rows[0])


@app.post(
    "/auth/users",
    tags=["auth"],
    summary="Create user (upsert by tenant+email)",
    operation_id="upsert_user",
    dependencies=[Depends(require_api_key)],
    response_model=UserOut,
)
# PUBLIC_INTERFACE
async def upsert_user(payload: UserCreate, db=Depends(get_request_db)) -> UserOut:
    """Create or update a user (upsert) scoped to tenant+email.

    Args:
        payload: UserCreate

    Returns:
        UserOut
    """
    rows = await db.fetch_all(
        """
        INSERT INTO users (tenant_id, email, full_name, auth_subject)
        VALUES (:tenant_id::uuid, :email, :full_name, :auth_subject)
        ON CONFLICT (tenant_id, lower(email))
        DO UPDATE SET full_name = EXCLUDED.full_name,
                      auth_subject = EXCLUDED.auth_subject,
                      updated_at = now()
        RETURNING id::text AS id,
                  tenant_id::text AS tenant_id,
                  email,
                  full_name,
                  status
        """,
        {
            "tenant_id": payload.tenant_id,
            "email": str(payload.email),
            "full_name": payload.full_name,
            "auth_subject": payload.auth_subject,
        },
    )
    return UserOut(**rows[0])


# -----------------------------
# Ingestion
# -----------------------------


@app.post(
    "/ingestion/meters",
    tags=["ingestion"],
    summary="Create a meter",
    operation_id="create_meter",
    dependencies=[Depends(require_api_key)],
    response_model=MeterOut,
)
# PUBLIC_INTERFACE
async def create_meter(payload: MeterCreate, db=Depends(get_request_db)) -> MeterOut:
    """Create a meter.

    Args:
        payload: MeterCreate

    Returns:
        MeterOut
    """
    rows = await db.fetch_all(
        """
        INSERT INTO meters (tenant_id, external_id, name, meter_type, unit, timezone, location)
        VALUES (:tenant_id::uuid, :external_id, :name, :meter_type, :unit, :timezone, :location)
        RETURNING id::text AS id,
                  tenant_id::text AS tenant_id,
                  external_id,
                  name,
                  meter_type,
                  unit,
                  timezone,
                  location
        """,
        payload.model_dump(),
    )
    return MeterOut(**rows[0])


@app.post(
    "/ingestion/readings",
    tags=["ingestion"],
    summary="Ingest meter readings (batch)",
    operation_id="ingest_readings",
    dependencies=[Depends(require_api_key)],
)
# PUBLIC_INTERFACE
async def ingest_readings(payload: ReadingsIngest, db=Depends(get_request_db)) -> Dict[str, Any]:
    """Batch ingest meter readings.

    Validation:
    - Enforces uniqueness (meter_id, reading_at) via DB unique index.

    Args:
        payload: ReadingsIngest

    Returns:
        Counts of inserted readings.
    """

    inserted = 0
    skipped = 0
    for r in payload.readings:
        try:
            await db.execute(
                """
                INSERT INTO meter_readings (tenant_id, meter_id, reading_at, value, quality, source)
                VALUES (:tenant_id::uuid, :meter_id::uuid, :reading_at, :value, :quality, :source)
                """,
                {
                    "tenant_id": payload.tenant_id,
                    "meter_id": payload.meter_id,
                    "reading_at": r.reading_at.isoformat(),
                    "value": str(r.value),
                    "quality": r.quality,
                    "source": r.source,
                },
            )
            inserted += 1
        except HTTPException:
            # Most common: unique violation. Treat as skipped.
            skipped += 1

    return {"inserted": inserted, "skipped": skipped, "total": len(payload.readings)}


@app.post(
    "/ingestion/documents",
    tags=["ingestion"],
    summary="Upload a document",
    operation_id="upload_document",
    dependencies=[Depends(require_api_key)],
)
# PUBLIC_INTERFACE
async def upload_document(
    tenant_id: str,
    uploaded_by_user_id: Optional[str] = None,
    document_type: str = "unknown",
    file: UploadFile = File(...),
    db=Depends(get_request_db),
) -> Dict[str, Any]:
    """Upload a document.

    Notes:
    - Stores extracted_text placeholder and uses a generated storage_key.

    Args:
        tenant_id: Tenant UUID (query param).
        uploaded_by_user_id: Optional user UUID.
        document_type: invoice|statement|contract|other|unknown.
        file: Multipart file.

    Returns:
        Document identifiers and status.
    """
    content = await file.read()

    if len(content) > 10 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="File too large (max 10MB)")

    storage_key = f"db://documents/{tenant_id}/{datetime.utcnow().isoformat()}_{file.filename}"

    # IMPORTANT: avoid casting NULL to uuid by using CASE expression.
    rows = await db.fetch_all(
        """
        INSERT INTO documents (
            tenant_id, uploaded_by_user_id, filename, content_type, storage_key, file_size_bytes,
            document_type, status, extracted_text
        )
        VALUES (
            :tenant_id::uuid,
            CASE WHEN :uploaded_by_user_id IS NULL THEN NULL ELSE :uploaded_by_user_id::uuid END,
            :filename, :content_type, :storage_key, :file_size_bytes,
            :document_type, 'uploaded', :extracted_text
        )
        RETURNING id::text AS id, status, created_at
        """,
        {
            "tenant_id": tenant_id,
            "uploaded_by_user_id": uploaded_by_user_id,
            "filename": file.filename,
            "content_type": file.content_type,
            "storage_key": storage_key,
            "file_size_bytes": len(content),
            "document_type": document_type,
            "extracted_text": content[:20000].decode("utf-8", errors="ignore"),
        },
    )
    return {"document_id": rows[0]["id"], "status": rows[0]["status"], "storage_key": storage_key}


# -----------------------------
# Orchestration: Analytics + Alerting
# -----------------------------


@app.post(
    "/orchestration/analyze",
    tags=["orchestration"],
    summary="Run analytics for a meter and store output",
    operation_id="run_analytics",
    dependencies=[Depends(require_api_key)],
    response_model=AnalyticsOutputOut,
)
# PUBLIC_INTERFACE
async def run_analytics(payload: AnalyticsRequest, db=Depends(get_request_db)) -> AnalyticsOutputOut:
    """Orchestrate analytics run.

    Steps:
    1) Fetch readings from DB.
    2) Send to analytics_service internal API for computation.
    3) Persist analytics_outputs.
    4) Optionally trigger an alert if anomaly score is high.

    Args:
        payload: AnalyticsRequest

    Returns:
        Stored AnalyticsOutputOut.
    """
    readings = await db.fetch_all(
        """
        SELECT reading_at, value
        FROM meter_readings
        WHERE tenant_id = :tenant_id::uuid
          AND meter_id = :meter_id::uuid
          AND reading_at >= :window_start
          AND reading_at <= :window_end
        ORDER BY reading_at ASC
        """,
        {
            "tenant_id": payload.tenant_id,
            "meter_id": payload.meter_id,
            "window_start": payload.window_start.isoformat(),
            "window_end": payload.window_end.isoformat(),
        },
    )

    if not readings:
        raise HTTPException(status_code=404, detail="No readings found for window")

    analytics_client = AnalyticsClient()
    try:
        result = await analytics_client.analyze(
            {
                "tenant_id": payload.tenant_id,
                "meter_id": payload.meter_id,
                "window_start": payload.window_start.isoformat(),
                "window_end": payload.window_end.isoformat(),
                "granularity": payload.granularity,
                "readings": readings,
            }
        )
    finally:
        await analytics_client.close()

    output_type = result.get("output_type", "baseline_anomaly")
    model_version = result.get("model_version")
    score = result.get("score")
    output = result.get("output", {})

    stored = await db.fetch_all(
        """
        INSERT INTO analytics_outputs (
            tenant_id, meter_id, output_type, granularity, window_start, window_end, model_version, output, score
        )
        VALUES (
            :tenant_id::uuid, :meter_id::uuid, :output_type, :granularity, :window_start, :window_end, :model_version,
            :output::jsonb, :score
        )
        RETURNING id::text AS id, output_type, granularity, window_start, window_end, score, output
        """,
        {
            "tenant_id": payload.tenant_id,
            "meter_id": payload.meter_id,
            "output_type": output_type,
            "granularity": payload.granularity,
            "window_start": payload.window_start.isoformat(),
            "window_end": payload.window_end.isoformat(),
            "model_version": model_version,
            "output": output,
            "score": score,
        },
    )

    stored_out = AnalyticsOutputOut(**stored[0])

    if result.get("should_alert") is True:
        alerting_client = AlertingClient()
        try:
            await alerting_client.trigger(
                {
                    "tenant_id": payload.tenant_id,
                    "meter_id": payload.meter_id,
                    "analytics_output_id": stored_out.id,
                    "severity": result.get("alert_severity", "warning"),
                    "title": result.get("alert_title", "Consumption anomaly detected"),
                    "message": result.get("alert_message", "An anomaly was detected in consumption."),
                    "channel": "in_app",
                }
            )
        finally:
            await alerting_client.close()

    return stored_out


@app.post(
    "/orchestration/alerts/trigger",
    tags=["orchestration"],
    summary="Trigger an alert through alerting_service",
    operation_id="trigger_alert",
    dependencies=[Depends(require_api_key)],
    response_model=AlertOut,
)
# PUBLIC_INTERFACE
async def trigger_alert(payload: AlertTrigger) -> AlertOut:
    """Trigger an alert through alerting_service.

    Args:
        payload: AlertTrigger

    Returns:
        AlertOut as created by alerting_service (and stored in DB).
    """
    alerting_client = AlertingClient()
    try:
        result = await alerting_client.trigger(payload.model_dump())
    finally:
        await alerting_client.close()
    return AlertOut(**result)


# -----------------------------
# UI-facing endpoints (aligned with frontend stubs)
# -----------------------------


@app.get(
    "/ui/meters",
    tags=["ui"],
    summary="List meters (UI)",
    operation_id="ui_list_meters",
    dependencies=[Depends(require_api_key)],
)
# PUBLIC_INTERFACE
async def ui_list_meters(
    tenant_id: str = Query(..., description="Tenant UUID."),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db=Depends(get_request_db),
) -> List[Dict[str, Any]]:
    """List meters for the tenant with an optional latest reading timestamp."""
    rows = await db.fetch_all(
        """
        SELECT m.id::text AS id,
               m.name AS name,
               (
                  SELECT MAX(reading_at)
                  FROM meter_readings mr
                  WHERE mr.meter_id = m.id
               ) AS last_reading_at
        FROM meters m
        WHERE m.tenant_id = :tenant_id::uuid
        ORDER BY m.created_at DESC
        LIMIT :limit OFFSET :offset
        """,
        {"tenant_id": tenant_id, "limit": limit, "offset": offset},
    )
    # Match frontend stub keys: { id, name, lastReadingAt? }
    out: List[Dict[str, Any]] = []
    for r in rows:
        out.append(
            {
                "id": r["id"],
                "name": r["name"],
                "lastReadingAt": r["last_reading_at"].isoformat() if r.get("last_reading_at") else None,
            }
        )
    return out


@app.get(
    "/ui/documents",
    tags=["ui"],
    summary="List documents (UI)",
    operation_id="ui_list_documents",
    dependencies=[Depends(require_api_key)],
    response_model=List[DocumentOut],
)
# PUBLIC_INTERFACE
async def ui_list_documents(
    tenant_id: str = Query(..., description="Tenant UUID."),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db=Depends(get_request_db),
) -> List[DocumentOut]:
    """List documents for the tenant."""
    rows = await db.fetch_all(
        """
        SELECT id::text AS id,
               filename AS name,
               tags,
               created_at AS uploaded_at,
               status
        FROM documents
        WHERE tenant_id = :tenant_id::uuid
          AND status <> 'deleted'
        ORDER BY created_at DESC
        LIMIT :limit OFFSET :offset
        """,
        {"tenant_id": tenant_id, "limit": limit, "offset": offset},
    )
    return [DocumentOut(**r) for r in rows]


def _month_window(now_utc: datetime) -> tuple[datetime, datetime]:
    """Return (start_of_month, now)."""
    start = now_utc.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return start, now_utc


def _prev_month_window(now_utc: datetime) -> tuple[datetime, datetime]:
    """Return (start_prev_month, end_prev_month)."""
    start_this, _ = _month_window(now_utc)
    end_prev = start_this - timedelta(microseconds=1)
    start_prev = end_prev.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return start_prev, end_prev


@app.get(
    "/ui/analytics/usage-summary",
    tags=["ui"],
    summary="Get usage summary KPIs (UI)",
    operation_id="ui_get_usage_summary",
    dependencies=[Depends(require_api_key)],
    response_model=UsageSummaryOut,
)
# PUBLIC_INTERFACE
async def ui_get_usage_summary(
    tenant_id: str = Query(..., description="Tenant UUID."),
    meter_id: str = Query(..., description="Meter UUID."),
    cost_per_kwh: float = Query(default=0.15, ge=0, description="Simple cost estimate."),
    db=Depends(get_request_db),
) -> UsageSummaryOut:
    """Compute usage KPI summary for current and previous month and latest anomaly score."""
    now = datetime.now(timezone.utc)
    this_start, this_end = _month_window(now)
    prev_start, prev_end = _prev_month_window(now)

    rows = await db.fetch_all(
        """
        SELECT
          COALESCE(SUM(CASE WHEN reading_at >= :this_start AND reading_at <= :this_end THEN value ELSE 0 END), 0)::float AS this_month,
          COALESCE(SUM(CASE WHEN reading_at >= :prev_start AND reading_at <= :prev_end THEN value ELSE 0 END), 0)::float AS prev_month
        FROM meter_readings
        WHERE tenant_id = :tenant_id::uuid
          AND meter_id = :meter_id::uuid
          AND reading_at >= :prev_start
          AND reading_at <= :this_end
        """,
        {
            "tenant_id": tenant_id,
            "meter_id": meter_id,
            "this_start": this_start.isoformat(),
            "this_end": this_end.isoformat(),
            "prev_start": prev_start.isoformat(),
            "prev_end": prev_end.isoformat(),
        },
    )
    this_month = float(rows[0]["this_month"]) if rows else 0.0
    prev_month = float(rows[0]["prev_month"]) if rows else 0.0

    score = 0.0
    analytics_client = AnalyticsClient()
    try:
        # Use a short window for anomaly score (last 14 days)
        win_end = now
        win_start = now - timedelta(days=14)
        anomaly = await analytics_client.anomalies(
            {
                "tenant_id": tenant_id,
                "meter_id": meter_id,
                "window_start": win_start.isoformat(),
                "window_end": win_end.isoformat(),
                "granularity": "daily",
            }
        )
        score = float(anomaly.get("score") or 0.0)
    except Exception:
        # Keep UI stable; anomaly may not be available if not enough data
        score = 0.0
    finally:
        await analytics_client.close()

    return UsageSummaryOut(
        kWhThisMonth=this_month,
        kWhLastMonth=prev_month,
        costThisMonth=this_month * float(cost_per_kwh),
        anomalyScore=score,
    )


@app.get(
    "/ui/analytics/benchmarking",
    tags=["ui"],
    summary="Get benchmarking metrics (UI)",
    operation_id="ui_get_benchmarking",
    dependencies=[Depends(require_api_key)],
    response_model=BenchmarkingOut,
)
# PUBLIC_INTERFACE
async def ui_get_benchmarking(
    tenant_id: str = Query(..., description="Tenant UUID."),
    meter_id: str = Query(..., description="Meter UUID."),
    window_days: int = Query(default=30, ge=7, le=365, description="Window size in days."),
) -> BenchmarkingOut:
    """Fetch benchmarking metrics from analytics_service and map to frontend stub shape."""
    now = datetime.now(timezone.utc)
    ws = now - timedelta(days=int(window_days))
    we = now

    analytics_client = AnalyticsClient()
    try:
        bench = await analytics_client.benchmark(
            {"tenant_id": tenant_id, "meter_id": meter_id, "window_start": ws.isoformat(), "window_end": we.isoformat()}
        )
    finally:
        await analytics_client.close()

    return BenchmarkingOut(
        percentile=float(bench.get("percentile") or 0.0),
        peerMedianKwh=float(bench.get("peer_median_daily") or 0.0),
        yourKwh=float(bench.get("your_avg_daily") or 0.0),
    )


def _map_severity_to_ui(severity: str) -> str:
    """Map service severity to UI stub severity."""
    if severity == "critical":
        return "high"
    if severity == "warning":
        return "medium"
    return "low"


@app.get(
    "/ui/alerts",
    tags=["ui"],
    summary="List alerts (UI)",
    operation_id="ui_list_alerts",
    dependencies=[Depends(require_api_key)],
    response_model=List[UiAlertOut],
)
# PUBLIC_INTERFACE
async def ui_list_alerts(
    tenant_id: str = Query(..., description="Tenant UUID."),
    user_id: Optional[str] = Query(default=None, description="Optional user UUID."),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> List[UiAlertOut]:
    """List alerts via alerting_service internal API and map to UI shape."""
    alerting_client = AlertingClient()
    try:
        rows = await alerting_client.list_alerts(
            {"tenant_id": tenant_id, "user_id": user_id, "limit": limit, "offset": offset}
        )
    finally:
        await alerting_client.close()

    out: List[UiAlertOut] = []
    for r in rows:
        status = str(r.get("status") or "open")
        out.append(
            UiAlertOut(
                id=str(r["id"]),
                severity=_map_severity_to_ui(str(r.get("severity") or "info")),
                title=str(r.get("title") or ""),
                description=str(r.get("message") or ""),
                createdAt=datetime.now(timezone.utc)  # fallback if not provided
                if r.get("created_at") is None
                else (
                    r["created_at"]
                    if isinstance(r["created_at"], datetime)
                    else datetime.fromisoformat(str(r["created_at"]).replace("Z", "+00:00"))
                ),
                acknowledged=(status == "acknowledged"),
            )
        )
    return out


@app.post(
    "/ui/alerts/{alert_id}/ack",
    tags=["ui"],
    summary="Acknowledge an alert (UI)",
    operation_id="ui_acknowledge_alert",
    dependencies=[Depends(require_api_key)],
)
# PUBLIC_INTERFACE
async def ui_acknowledge_alert(alert_id: str) -> Dict[str, Any]:
    """Acknowledge an alert via alerting_service internal API."""
    alerting_client = AlertingClient()
    try:
        return await alerting_client.ack_alert(alert_id)
    finally:
        await alerting_client.close()


@app.exception_handler(Exception)
async def unhandled_exception_handler(_: Any, exc: Exception) -> JSONResponse:
    """Keep response predictable for clients."""
    return JSONResponse(status_code=500, content={"error": "internal_server_error", "detail": str(exc)})
