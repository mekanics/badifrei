FROM python:3.12-slim

WORKDIR /app

RUN pip install uv

COPY pyproject.toml ./
COPY api/ ./api/
COPY ml/ ./ml/
COPY collector/ ./collector/

RUN uv pip install --system -e "."

EXPOSE 8000

CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
