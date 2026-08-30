FROM python:3.11-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DEBIAN_FRONTEND=noninteractive

WORKDIR /app

# Install system dependencies for headless map rendering & geo calculations
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libfreetype6-dev \
    libpng-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy dependencies manifest and install python packages
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code and default data
COPY . .

# Create persistent data directory
RUN mkdir -p /app/data

# Default volumes
VOLUME ["/app/data"]

CMD ["python", "main.py"]
