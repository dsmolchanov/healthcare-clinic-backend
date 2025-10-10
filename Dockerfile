# Dockerfile for WhatsApp Webhook Server
# Multi-stage build for smaller image and faster startup
FROM python:3.11-slim as builder

# Set working directory
WORKDIR /app

# Install build dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    g++ \
    libxml2-dev \
    libxslt1-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for better caching
COPY requirements.txt .

# Install Python dependencies in a virtual environment
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir --default-timeout=100 --prefer-binary -r requirements.txt

# Final stage
FROM python:3.11-slim

# Install only runtime dependencies
RUN apt-get update && apt-get install -y \
    tesseract-ocr \
    libmagic1 \
    libxml2 \
    libxslt1.1 \
    poppler-utils \
    && rm -rf /var/lib/apt/lists/*

# Copy virtual environment from builder
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Set working directory
WORKDIR /app


# Copy application code
COPY app/ ./app/
COPY main.py .
COPY run_worker.py .

# Expose port (Fly.io uses 8080 by default)
EXPOSE 8080

# Set Python to unbuffered mode for better logging
ENV PYTHONUNBUFFERED=1

# Run the application with optimized settings
CMD ["python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080", "--log-level", "info", "--timeout-keep-alive", "75", "--workers", "1"]
