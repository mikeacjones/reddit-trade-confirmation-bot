# syntax=docker/dockerfile:1
FROM python:3.12-slim

WORKDIR /app

# Install dependencies
COPY src/temporal/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code
COPY src/ ./src/
COPY src/mdtemplates/ ./src/mdtemplates/

WORKDIR /app/src

# Default to running the worker
# Override TEMPORAL_HOST to point to your Temporal server
ENV TEMPORAL_HOST=host.docker.internal:7233

CMD ["python", "-m", "temporal.worker"]
