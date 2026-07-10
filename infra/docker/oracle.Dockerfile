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
RUN uv sync --frozen --no-dev --project apps/oracle --no-install-workspace

COPY apps/runtime apps/runtime
COPY apps/oracle apps/oracle
COPY packages/contracts/python packages/contracts/python
COPY evals evals
RUN uv sync --frozen --no-dev --project apps/oracle

CMD ["trust-oracle", "worker", "--max-jobs", "0"]
