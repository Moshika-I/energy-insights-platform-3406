# Project Repository

This repository contains multiple services (backend API, analytics, alerting, database, and frontend).

## E2E smoke check (repo short command)

A low-output end-to-end smoke check script is available:

```bash
./scripts/smoke-check
```

- Writes per-step HTTP responses to `/tmp/smoke-check/` (override via `SMOKE_ARTIFACT_DIR`).
- Uses/needs `BACKEND_API_KEY` (and `POSTGRES_URL` if it needs to start services locally).
- You can also point it at already-running services:

```bash
BACKEND_BASE_URL="http://127.0.0.1:8000" ./scripts/smoke-check
```