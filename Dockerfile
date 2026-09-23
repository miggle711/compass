FROM python:3.12-slim

RUN pip install --no-cache-dir uv

WORKDIR /app

COPY pyproject.toml uv.lock ./
COPY src/ src/

RUN uv sync --frozen --no-dev

# Cloud Run injects PORT at runtime; default to 8080 for local docker run.
ENV PORT=8080
EXPOSE 8080

CMD ["sh", "-c", "uv run uvicorn compass.api:app --host 0.0.0.0 --port ${PORT}"]
