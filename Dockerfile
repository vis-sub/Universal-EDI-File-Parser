# Universal EDI Parser service
#   docker build -t universal-edi-parser .
#   docker run --rm -p 8080:8080 universal-edi-parser
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    EDIPARSE_HOST=0.0.0.0 \
    EDIPARSE_PORT=8080

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install ".[service]" && useradd --create-home --uid 10001 app

USER app
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=2)"
CMD ["ediparse", "serve"]
