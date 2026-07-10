FROM python:3.12.10-slim-bookworm

COPY --from=ghcr.io/astral-sh/uv:0.10.12 /uv /uvx /bin/

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY apps/runtime/pyproject.toml apps/runtime/pyproject.toml
COPY apps/oracle/pyproject.toml apps/oracle/pyproject.toml
COPY packages/contracts/python/pyproject.toml packages/contracts/python/pyproject.toml
RUN uv sync --frozen --no-dev --project apps/runtime --no-install-workspace

COPY apps/runtime apps/runtime
COPY packages/contracts/python packages/contracts/python
COPY evals evals
COPY alembic.ini alembic.ini
RUN uv sync --frozen --no-dev --project apps/runtime && playwright install --with-deps chromium

EXPOSE 8000
CMD ["uvicorn", "trust_runtime.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
