from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional

from fastapi import Depends, FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from src.api.clients.alerting_client import AlertingClient
from src.api.clients.analytics_client import AnalyticsClient
from src.api.core.db import get_db, lifespan
from src.api.core.security import require_api_key
from src.api.schemas.alerts import AlertOut, AlertTrigger
from src.api.schemas.analytics import AnalyticsOutputOut, AnalyticsRequest
from src.api.schemas.meters import MeterCreate, MeterOut
from src.api.schemas.readings import ReadingsIngest
from src.api.schemas.tenants import TenantCreate, TenantOut
from src.api.schemas.users import UserCreate, UserOut

openapi_tags = [
    {"name": "health", "description": "Service health and diagnostics."},
    {"name": "auth", "description": "Template auth/user provisioning endpoints (API-key protected)."},
    {"name": "ingestion", "description": "Data ingestion endpoints for meters, readings, and documents."},
    {"name": "orchestration", "description": "Orchestration endpoints that call analytics and alerting services."},
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

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/", tags=["health"], summary="Health check", operation_id="health_check")
# PUBLIC_INTERFACE
def health_check() -> Dict[str, str]:
    """Health check endpoint.

    Returns:
        JSON with a simple status message.
    """
    return {"message": "Healthy"}


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
async def create_tenant(payload: TenantCreate) -> TenantOut:
    """Create a tenant.

    Args:
        payload: TenantCreate request body.

    Returns:
        Newly created tenant.
    """
    db = get_db()
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
async def upsert_user(payload: UserCreate) -> UserOut:
    """Create or update a user (upsert) scoped to tenant+email.

    Args:
        payload: UserCreate

    Returns:
        UserOut
    """
    db = get_db()
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
async def create_meter(payload: MeterCreate) -> MeterOut:
    """Create a meter.

    Args:
        payload: MeterCreate

    Returns:
        MeterOut
    """
    db = get_db()
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
async def ingest_readings(payload: ReadingsIngest) -> Dict[str, Any]:
    """Batch ingest meter readings.

    Validation:
    - Enforces uniqueness (meter_id, reading_at) via DB unique index.

    Args:
        payload: ReadingsIngest

    Returns:
        Counts of inserted readings.
    """
    db = get_db()

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
) -> Dict[str, Any]:
    """Upload a document.

    Notes:
    - This template stores the document content in DB as extracted_text placeholder
      and uses a generated storage_key. In production, this should store to S3/GCS.

    Args:
        tenant_id: Tenant UUID (query param).
        uploaded_by_user_id: Optional user UUID.
        document_type: invoice|statement|contract|other|unknown.
        file: Multipart file.

    Returns:
        Document identifiers and status.
    """
    db = get_db()
    content = await file.read()

    # Minimal safety checks
    if len(content) > 10 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="File too large (max 10MB)")

    storage_key = f"db://documents/{tenant_id}/{datetime.utcnow().isoformat()}_{file.filename}"

    rows = await db.fetch_all(
        """
        INSERT INTO documents (
            tenant_id, uploaded_by_user_id, filename, content_type, storage_key, file_size_bytes,
            document_type, status, extracted_text
        )
        VALUES (
            :tenant_id::uuid, :uploaded_by_user_id::uuid, :filename, :content_type, :storage_key, :file_size_bytes,
            :document_type, 'uploaded', :extracted_text
        )
        RETURNING id::text AS id, status
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
async def run_analytics(payload: AnalyticsRequest) -> AnalyticsOutputOut:
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
    db = get_db()
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

    # Trigger alert if the analytics service says so
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


@app.exception_handler(Exception)
async def unhandled_exception_handler(_: Any, exc: Exception) -> JSONResponse:
    # Keep response predictable for clients
    return JSONResponse(status_code=500, content={"error": "internal_server_error", "detail": str(exc)})
