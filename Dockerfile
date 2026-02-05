# syntax=docker/dockerfile:1
FROM python:3.12-slim

WORKDIR /app

# Install dependencies
COPY src/temporal/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code
COPY src/ ./src/
COPY src/mdtemplates/ ./src/mdtemplates/
COPY entrypoint.sh ./entrypoint.sh

WORKDIR /app/src

# Override TEMPORAL_HOST to point to your Temporal server
ENV TEMPORAL_HOST=host.docker.internal:7233

ENTRYPOINT ["/app/entrypoint.sh"]
