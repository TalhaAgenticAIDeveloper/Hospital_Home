#!/bin/bash
set -e

# ==============================================================================
# Healthcare SaaS Backend - Container Startup Script
# ==============================================================================

echo "=================================================="
echo " Starting Healthcare SaaS Backend Service"
echo "=================================================="

# Allow executing custom container commands (e.g., pytest, custom scripts, or bash)
if [ "$#" -gt 0 ]; then
    echo "Custom command provided. Executing: $@"
    exec "$@"
fi

# Ensure all upload subdirectories exist inside container
mkdir -p uploads/documents \
         uploads/doctor_documents \
         uploads/consultation_audio \
         uploads/patient_documents \
         uploads/meeting_transcripts 2>/dev/null || true

# ------------------------------------------------------------------------------
# 1. Wait for Database Readiness
# ------------------------------------------------------------------------------
echo "Verifying database connectivity..."
python << 'EOF'
import asyncio
import os
import sys
import time
from urllib.parse import urlparse
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text

db_url = os.environ.get("DATABASE_URL", "")

if not db_url:
    print("ERROR: DATABASE_URL environment variable is not set!", file=sys.stderr)
    sys.exit(1)

# Mask credentials for safe logging
try:
    parsed = urlparse(db_url)
    user = parsed.username or ""
    netloc = f"{user}:****@{parsed.hostname}:{parsed.port}" if user else f"{parsed.hostname}:{parsed.port}"
    print(f"Connecting to database at {parsed.scheme}://{netloc}{parsed.path}...")
except Exception:
    print("Connecting to database...")

max_seconds = int(os.environ.get("DB_CONNECT_TIMEOUT", "60"))
start_time = time.time()

async def check_db():
    engine = create_async_engine(db_url, pool_pre_ping=True)
    attempt = 0
    while time.time() - start_time < max_seconds:
        attempt += 1
        try:
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            print(">> Database connection successfully established!")
            await engine.dispose()
            return True
        except Exception as e:
            if attempt % 5 == 1:
                print(f"Waiting for database to be ready (attempt {attempt}): {e}")
            await asyncio.sleep(2)

    await engine.dispose()
    print(f"ERROR: Database was not reachable within {max_seconds} seconds!", file=sys.stderr)
    return False

if not asyncio.run(check_db()):
    sys.exit(1)
EOF

# ------------------------------------------------------------------------------
# 2. Apply Database Migrations (Alembic)
# ------------------------------------------------------------------------------
if [ "${SKIP_MIGRATIONS:-false}" != "true" ]; then
    echo "Applying database migrations (alembic upgrade head)..."
    alembic upgrade head
    echo ">> Database migrations completed successfully."
else
    echo "Skipping migrations (SKIP_MIGRATIONS is set to true)."
fi

# ------------------------------------------------------------------------------
# 3. Start Uvicorn Server
# ------------------------------------------------------------------------------
APP_HOST="${HOST:-0.0.0.0}"
APP_PORT="${PORT:-8000}"
APP_WORKERS="${WORKERS:-1}"

RELOAD_FLAG=""
if [ "${DEBUG:-false}" = "true" ] || [ "${DEBUG:-false}" = "True" ] || [ "${RELOAD:-false}" = "true" ]; then
    echo "Enabling auto-reload mode..."
    RELOAD_FLAG="--reload"
    APP_WORKERS=1
fi

echo "Starting Uvicorn on ${APP_HOST}:${APP_PORT} (workers: ${APP_WORKERS})..."
exec uvicorn app.main:app \
    --host "${APP_HOST}" \
    --port "${APP_PORT}" \
    --workers "${APP_WORKERS}" \
    --proxy-headers \
    --forwarded-allow-ips='*' \
    ${RELOAD_FLAG}
