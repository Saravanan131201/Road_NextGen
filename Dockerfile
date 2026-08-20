# ── Stage 1: Python 3.11 slim base ──────────────────
FROM python:3.11-slim

# System dependencies needed by OpenCV and torch
RUN apt-get update && apt-get install -y --no-install-recommends \
    libglib2.0-0 \
    libgl1-mesa-glx \
    libgthread-2.0-0 \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy requirements first (Docker layer cache — only re-installs if requirements change)
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copy rest of the project
COPY . .

# Render sets PORT automatically — expose it
EXPOSE 10000

# Start with gunicorn + eventlet worker (required for Flask-SocketIO)
CMD gunicorn --worker-class eventlet \
             --workers 1 \
             --bind 0.0.0.0:${PORT:-10000} \
             --timeout 120 \
             --keep-alive 5 \
             --log-level info \
             wsgi:app