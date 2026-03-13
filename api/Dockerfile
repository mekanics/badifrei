FROM python:3.12-slim

WORKDIR /app

RUN pip install uv && apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
COPY api/ ./api/
COPY ml/ ./ml/
COPY collector/ ./collector/

RUN uv pip install --system -e "."

# Run as non-root user
# model_artifacts and static/templates are mounted as volumes; appuser needs read access.
# Named volumes are initialised with the image directory's permissions on first use.
RUN adduser --disabled-password --gecos "" appuser \
    && mkdir -p /app/ml/models \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
