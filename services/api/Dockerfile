# Build from the public monorepo root:
#   docker build -f services/api/Dockerfile .
FROM python:3.12-slim@sha256:57cd7c3a7a273101a6485ba99423ee568157882804b1124b4dd04266317710de

COPY --from=ghcr.io/astral-sh/uv:latest@sha256:77280f2f771df71f90786c314fe1bbc1e023feac652969bbf139c280babf2eb7 /uv /usr/local/bin/uv

ARG TARGETARCH
ARG TECTONIC_VERSION=0.15.0
ARG TECTONIC_SHA256_AMD64=dfb82876f2986862996e564fa507a9e576e0c1e3bee63c2c1bd677c2543e6407
ARG TECTONIC_SHA256_ARM64=1f59f9fb8eb65e8ba18658fc9016767e7d3e12488ded8b8fffa34254e51ce42c

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_CACHE_DIR=/tmp/uv-cache \
    SIX_DATA_DIR=/data \
    TECTONIC_CACHE_DIR=/data/tectonic-cache \
    XDG_CACHE_HOME=/data/tectonic-cache

# ffmpeg/ffprobe support interview media; Tectonic and SyncTeX support Writer.
# Every downloaded executable is version- and digest-pinned for both supported
# architectures. texlive-binaries supplies the standalone `synctex` command.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl ffmpeg texlive-binaries \
    && case "$TARGETARCH" in \
         amd64) tectonic_arch="x86_64"; tectonic_sha="$TECTONIC_SHA256_AMD64" ;; \
         arm64) tectonic_arch="aarch64"; tectonic_sha="$TECTONIC_SHA256_ARM64" ;; \
         *) echo "unsupported Tectonic target architecture: $TARGETARCH" >&2; exit 1 ;; \
       esac \
    && tectonic_archive="/tmp/tectonic-${TECTONIC_VERSION}.tar.gz" \
    && curl --fail --silent --show-error --location \
         "https://github.com/tectonic-typesetting/tectonic/releases/download/tectonic%40${TECTONIC_VERSION}/tectonic-${TECTONIC_VERSION}-${tectonic_arch}-unknown-linux-musl.tar.gz" \
         --output "$tectonic_archive" \
    && echo "$tectonic_sha  $tectonic_archive" | sha256sum --check --strict \
    && tar -xzf "$tectonic_archive" -C /usr/local/bin tectonic \
    && tectonic --version >/dev/null \
    && command -v ffmpeg >/dev/null \
    && command -v ffprobe >/dev/null \
    && command -v synctex >/dev/null \
    && apt-get purge -y curl \
    && apt-get autoremove -y \
    && find /var/lib/apt/lists -mindepth 1 -delete \
    && rm -f "$tectonic_archive"

RUN useradd --create-home --uid 10001 sixsentences \
    && mkdir -p /data /privacy /workspace/services/api \
    && chown sixsentences:sixsentences /data /privacy

# Install the local public engine and the server from one hash-bound uv lock.
# The build fails if the root engine is not exactly 0.2.0a1.
WORKDIR /workspace
COPY pyproject.toml README.md LICENSE NOTICE THIRD_PARTY_NOTICES.md /workspace/
COPY src /workspace/src
COPY services/api/pyproject.toml services/api/README.md services/api/uv.lock /workspace/services/api/
RUN uv sync --project /workspace/services/api --frozen --no-dev --no-install-project --no-editable

COPY services/api/src /workspace/services/api/src
COPY services/api/alembic.ini /workspace/services/api/alembic.ini
COPY services/api/alembic /workspace/services/api/alembic
RUN uv sync --project /workspace/services/api --frozen --no-dev --no-editable \
    && uv cache clean

ENV PATH="/workspace/services/api/.venv/bin:${PATH}"
WORKDIR /workspace/services/api
USER sixsentences
EXPOSE 8000

CMD ["uvicorn", "sixsentences_server.api.app:app", "--host", "0.0.0.0", "--port", "8000", "--ws", "websockets-sansio", "--ws-max-size", "16384", "--ws-max-queue", "8", "--ws-per-message-deflate", "false"]
