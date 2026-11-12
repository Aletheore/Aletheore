# Tunnel registry image. Build context: the repository root (see compose.registry.yml). Server-only dependencies, nothing else.
FROM python:3.12-slim-bookworm AS build
WORKDIR /src
COPY src/pyproject.toml src/README.md src/LICENSE ./
COPY src/aletheore/__init__.py ./aletheore/__init__.py
COPY src/aletheore/council ./aletheore/council
RUN pip install --no-cache-dir --prefix=/install "fastapi>=0.143,<0.144" "uvicorn>=0.53,<1.0" "httpx>=0.27" "pynacl>=1.5" \
 && pip install --no-cache-dir --prefix=/install --no-deps .

FROM python:3.12-slim-bookworm
RUN useradd --system --uid 10002 --no-create-home --shell /usr/sbin/nologin registry \
 && mkdir -p /data && chown -R registry:registry /data
COPY --from=build /install /usr/local
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 COUNCIL_REGISTRY_DATA=/data COUNCIL_REGISTRY_PORT=18700
USER 10002:10002
EXPOSE 18700
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD ["python", "-c", "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:18700/v1/status', timeout=3).status == 200 else 1)"]
CMD ["python", "-m", "aletheore.council.registry_main"]
