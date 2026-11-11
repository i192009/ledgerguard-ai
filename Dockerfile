FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app/src
WORKDIR /app
RUN pip install --no-cache-dir uv
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project
ENV PATH="/app/.venv/bin:$PATH"
COPY . .
RUN useradd --create-home ledgerguard && mkdir -p artifacts && chown ledgerguard:ledgerguard /app /app/artifacts
USER ledgerguard
EXPOSE 8000
CMD ["uvicorn", "ledgerguard.api:app", "--host", "0.0.0.0", "--port", "8000"]
