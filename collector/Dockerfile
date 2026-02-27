FROM python:3.12-slim

WORKDIR /app

# Install uv for fast installs
RUN pip install uv

# Copy dependency files
COPY pyproject.toml ./
COPY collector/ ./collector/
COPY ml/ ./ml/

# Install production deps only (no dev extras)
RUN uv pip install --system -e "."

CMD ["python", "-m", "collector.main"]
