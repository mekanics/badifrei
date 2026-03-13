FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml uv.lock ./
# Install only production deps (no dev extras)
RUN pip install uv && uv pip install --system -e "."
COPY . .

# Run as non-root user
# model_artifacts volume is mounted at /app/ml/models; directory pre-created here
# so the named volume is initialised with appuser ownership.
RUN adduser --disabled-password --gecos "" appuser \
    && mkdir -p /app/ml/models \
    && chown -R appuser:appuser /app
USER appuser

CMD ["python", "-m", "ml.retrain"]
