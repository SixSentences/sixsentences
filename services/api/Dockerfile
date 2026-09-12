FROM ghcr.io/astral-sh/uv:0.10.12 AS uv
FROM python:3.12.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    SIX_COMMUNITY_DATABASE_URL=sqlite:////data/community.sqlite3

WORKDIR /workspace

COPY --from=uv /uv /uvx /bin/

COPY pyproject.toml README.md LICENSE NOTICE THIRD_PARTY_NOTICES.md ./
COPY src ./src
COPY services/api ./services/api

RUN uv sync --project services/api --frozen --no-dev --no-editable \
    && addgroup --system --gid 10001 community \
    && adduser --system --uid 10001 --ingroup community --home /nonexistent community \
    && mkdir -p /data \
    && chown community:community /data

USER community
ENV PATH="/workspace/services/api/.venv/bin:${PATH}"
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/ready', timeout=3).read()"]

CMD ["six-community", "serve", "--host", "0.0.0.0", "--port", "8000"]
