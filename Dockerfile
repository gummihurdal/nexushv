FROM python:3.12-slim

LABEL maintainer="NexusHV Team"
LABEL description="NexusHV Hypervisor Management API"
LABEL version="2.0.0"

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    libvirt-dev pkg-config gcc curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application
COPY api/ api/
COPY ai/ ai/
COPY ha/ ha/
COPY ui/dist/ ui/dist/

# Create data and log directories
RUN mkdir -p data logs

# Expose ports
EXPOSE 8080 8081

# Health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8080/health || exit 1

# Default: run API
CMD ["python3", "api/nexushv_api.py", "--host", "0.0.0.0", "--port", "8080"]
