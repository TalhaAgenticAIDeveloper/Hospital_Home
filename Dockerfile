# ==============================================================================
# Healthcare SaaS Backend Dockerfile
# Python 3.12 (Debian Bookworm Slim)
# ==============================================================================

FROM python:3.12-slim

# Prevent Python from writing bytecode files (.pyc) and buffer stdout/stderr
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Install system dependencies:
# - curl: needed for container healthchecks
# - gcc, libpq-dev, libffi-dev: C build tools and headers
# - netcat-openbsd: networking utility
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    gcc \
    libpq-dev \
    libffi-dev \
    netcat-openbsd \
    && rm -rf /var/lib/apt/lists/*

# Optimize Docker layer caching: install Python dependencies first
COPY requirements.txt /app/requirements.txt
RUN pip install --upgrade pip && \
    pip install -r /app/requirements.txt

# Create a dedicated non-root user and group
RUN groupadd -g 1000 appgroup && \
    useradd -u 1000 -g appgroup -m -s /bin/bash appuser

# Copy application code into container
COPY . /app

# Ensure start.sh has Unix line endings (LF) and executable permissions
RUN sed -i 's/\r$//' /app/start.sh && \
    chmod +x /app/start.sh

# Create upload directory structure and assign ownership to appuser
RUN mkdir -p /app/uploads/documents \
             /app/uploads/doctor_documents \
             /app/uploads/consultation_audio \
             /app/uploads/patient_documents \
             /app/uploads/meeting_transcripts && \
    chown -R appuser:appgroup /app

# Run as non-root user for security
USER appuser

# Expose FastAPI application port
EXPOSE 8000

# Container healthcheck using FastAPI /health endpoint
HEALTHCHECK --interval=20s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Start container via startup script
ENTRYPOINT ["/app/start.sh"]
