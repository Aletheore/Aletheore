# Council room server image. Build context: the repository root (see compose.yml).
# Server-only dependencies are installed (no terminal UI, no agent tooling), so the image stays small.
FROM python:3.12-slim-bookworm AS build
WORKDIR /src
COPY src/pyproject.toml src/README.md src/LICENSE ./
COPY src/aletheore/__init__.py ./aletheore/__init__.py
COPY src/aletheore/council ./aletheore/council
RUN pip install --no-cache-dir --prefix=/install "fastapi>=0.143,<0.144" "uvicorn>=0.53,<1.0" "psycopg[binary,pool]>=3.2,<4" "uvloop>=0.21" "httptools>=0.6" \
 && pip install --no-cache-dir --prefix=/install --no-deps .

FROM python:3.12-slim-bookworm
RUN useradd --system --uid 10001 --no-create-home --shell /usr/sbin/nologin council \
 && mkdir -p /data/bundles && chown -R council:council /data
COPY --from=build /install /usr/local
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    COUNCIL_HOST=0.0.0.0 COUNCIL_ALLOW_PUBLIC=1 COUNCIL_PORT=8765 \
    COUNCIL_DB=/data/server.db COUNCIL_BUNDLE_DIR=/data/bundles
USER 10001:10001
EXPOSE 8765
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=3 \
  CMD ["python", "-c", "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8765/healthz', timeout=3).status == 200 else 1)"]
CMD ["python", "-m", "aletheore.council.server_main"]
