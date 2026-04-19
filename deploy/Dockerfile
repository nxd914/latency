# Kinzie trading daemon — Python 3.11-slim
FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y \
    build-essential \
    libssl-dev \
    git \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml .
RUN touch README.md
RUN pip install --no-cache-dir .

# Copy source
COPY . .

# Runtime config
ENV PYTHONUNBUFFERED=1
ENV EXECUTION_MODE=paper
ENV PYTHONPATH=/app/..

HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD python -c "import sys; sys.exit(0)"

CMD ["python", "daemon.py"]
