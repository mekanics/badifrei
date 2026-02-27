FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN pip install uv && uv pip install --system -e ".[dev]"
COPY . .
CMD ["python", "-m", "ml.retrain"]
