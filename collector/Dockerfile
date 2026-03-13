FROM python:3.12-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y curl && rm -rf /var/lib/apt/lists/*

# Install uv for fast installs
RUN pip install uv

# Copy dependency files
COPY pyproject.toml ./
COPY collector/ ./collector/
COPY ml/ ./ml/
COPY scripts/ ./scripts/
COPY docker/migrations/ ./docker/migrations/

# Install production deps only (no dev extras)
RUN uv pip install --system -e "."

CMD ["python", "-m", "collector.main"]
