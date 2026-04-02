FROM python:3.14-slim
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN pip install uv && uv pip install --system -e "."
COPY . .

CMD ["python", "-m", "ml.retrain"]
