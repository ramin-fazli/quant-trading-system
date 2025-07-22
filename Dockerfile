# Multi-stage optimized Dockerfile for Python Trading System
FROM python:3.11-slim as base

# Set environment variables for Python optimization
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/app

# Install system dependencies in one layer to reduce image size
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    wget \
    gcc \
    g++ \
    make \
    libpq-dev \
    pkg-config \
    default-libmysqlclient-dev \
    build-essential \
    git \
    redis-tools \
    netcat-openbsd \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

# Create app user with specific UID/GID for consistency
RUN groupadd -r app -g 1000 && useradd -r -g app -u 1000 -m -s /bin/bash app

# Set work directory
WORKDIR /app

# Copy and install Python dependencies first (for better caching)
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip setuptools wheel && \
    pip install --no-cache-dir -r requirements.txt

# Install additional performance packages
RUN pip install --no-cache-dir \
    gunicorn[gevent] \
    uvloop \
    psycopg2-binary \
    redis \
    prometheus-client

# Copy application code
COPY . .

# Create necessary directories with proper permissions
RUN mkdir -p logs backtest_reports data state_fallback cache config && \
    chown -R app:app /app && \
    chmod -R 755 /app

# Switch to app user
USER app

# Add health check script
RUN echo '#!/bin/bash\ncurl -f http://localhost:8050/health || exit 1' > /app/healthcheck.sh && \
    chmod +x /app/healthcheck.sh

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD ["/app/healthcheck.sh"]

# Expose ports
EXPOSE 8050 8000

# Add build arguments for metadata
ARG BUILD_DATE
ARG VCS_REF  
ARG BUILD_VERSION

# Add labels for better container management
LABEL maintainer="Trading System Team" \
      org.label-schema.build-date=$BUILD_DATE \
      org.label-schema.vcs-ref=$VCS_REF \
      org.label-schema.version=$BUILD_VERSION \
      org.label-schema.schema-version="1.0" \
      org.label-schema.name="trading-system" \
      org.label-schema.description="Advanced pairs trading system with ML capabilities"

# Create startup script
RUN echo '#!/bin/bash\n\
set -e\n\
\n\
echo "🚀 Starting Trading System..."\n\
echo "Environment: ${TRADING_MODE:-live}"\n\
echo "Log Level: ${LOG_LEVEL:-INFO}"\n\
\n\
# Wait for dependencies\n\
if [ -n "$WAIT_FOR_INFLUXDB" ]; then\n\
    echo "⏳ Waiting for InfluxDB..."\n\
    timeout 60 bash -c "until curl -f http://influxdb:8086/health; do sleep 2; done"\n\
    echo "✅ InfluxDB is ready!"\n\
fi\n\
\n\
if [ -n "$WAIT_FOR_REDIS" ]; then\n\
    echo "⏳ Waiting for Redis (optional)..."\n\
    timeout 30 bash -c "until nc -z redis 6379; do sleep 2; done" || echo "⚠️  Redis not available, continuing without it"\n\
fi\n\
\n\
# Start the application based on mode\n\
if [ "${TRADING_MODE}" = "dashboard" ]; then\n\
    echo "📊 Starting Dashboard only..."\n\
    exec python -m dashboard.dashboard_manager\n\
elif [ "${TRADING_MODE}" = "backtest" ]; then\n\
    echo "📈 Starting Backtest mode..."\n\
    exec python -m scripts.pair_trading.main --mode backtest --data-provider ${DATA_PROVIDER:-ctrader} --broker ${BROKER:-ctrader}\n\
else\n\
    echo "💹 Starting Live Trading mode..."\n\
    exec python -m scripts.pair_trading.main --mode live --data-provider ${DATA_PROVIDER:-ctrader} --broker ${BROKER:-ctrader}\n\
fi' > /app/entrypoint.sh && chmod +x /app/entrypoint.sh

# Default command
ENTRYPOINT ["/app/entrypoint.sh"]
