# Universal EDI Parser service
#   docker build -t universal-edi-file-parser .
#   docker run --rm -p 8080:8080 universal-edi-file-parser
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    EDIPARSE_HTTP_HOST=0.0.0.0 \
    EDIPARSE_HTTP_PORT=8080

# Pick up Debian security fixes released since the base image was built.
# hadolint ignore=DL3005
RUN apt-get update && apt-get upgrade -y --no-install-recommends && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md LICENSE constraints-service.txt ./
COPY src ./src
# Dependency versions come from constraints-service.txt so every build is reproducible.
# hadolint ignore=DL3013
RUN pip install --constraint constraints-service.txt ".[service]" \
 && useradd --create-home --uid 10001 --user-group app

# Numeric UID so Kubernetes can verify runAsNonRoot.
USER 10001:10001
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s --start-period=30s --start-interval=2s \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=2)"]
CMD ["ediparse", "serve"]
