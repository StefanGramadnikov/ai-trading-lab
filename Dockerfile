FROM ghcr.io/astral-sh/uv:0.11.7 AS uv
FROM python:3.12.13-slim-bookworm
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_PYTHON_DOWNLOADS=0 PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv sync --locked --no-dev --no-editable && useradd --uid 10001 --create-home app
COPY alembic.ini ./
COPY migrations ./migrations
USER app
EXPOSE 8000
CMD ["/app/.venv/bin/uvicorn", "ai_trading_lab.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
