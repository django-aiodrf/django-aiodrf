# syntax=docker/dockerfile:1
FROM ghcr.io/astral-sh/uv:0.12.19 AS uv
FROM python:3.12-slim-bookworm

COPY --from=uv /uv /usr/local/bin/uv
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_NO_CACHE=1 \
    PATH="/opt/venv/bin:$PATH"
RUN useradd --create-home --uid 10001 example \
    && mkdir /data && chown example:example /data
WORKDIR /workspace
COPY --chown=example:example pyproject.toml README.md LICENSE ./
COPY --chown=example:example src ./src
COPY --chown=example:example examples ./examples
ARG EXAMPLE=basics
WORKDIR /workspace/examples/${EXAMPLE}
RUN uv venv /opt/venv \
    && uv pip install --python /opt/venv/bin/python \
        -r pyproject.toml --group test -e ../.. 'uvicorn[standard]>=0.30'
USER example
EXPOSE 8000
HEALTHCHECK --interval=3s --timeout=2s --start-period=60s --retries=20 \
    CMD python -c "import socket; socket.create_connection(('127.0.0.1', 8000), timeout=1).close()"
ENTRYPOINT ["python", "/workspace/examples/container_entrypoint.py"]
