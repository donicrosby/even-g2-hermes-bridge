FROM python:3.12-slim-bookworm

RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir uv

WORKDIR /app
RUN groupadd -r hermes && useradd -r -g hermes -m -d /home/hermes hermes

COPY pyproject.toml uv.lock ./
COPY src ./src
RUN uv sync --frozen --no-dev

ENV PATH="/app/.venv/bin:${PATH}"
ENV UV_CACHE_DIR=/home/hermes/.cache/uv

RUN chown -R hermes:hermes /app /home/hermes

USER hermes
EXPOSE 8765

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8765/health')" || exit 1

CMD ["python", "-m", "uvicorn", "byoa_bridge.server:app", "--host", "0.0.0.0", "--port", "8765"]
