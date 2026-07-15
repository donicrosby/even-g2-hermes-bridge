# -- Builder stage for webrtcvad compilation --------------------------------------
FROM python:3.12-slim-bookworm AS builder
WORKDIR /build
RUN apt-get update && apt-get install -y --no-install-recommends gcc python3-dev && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir uv
COPY requirements.txt .
RUN uv pip install --system --no-cache-dir -r requirements.txt

# -- Runtime stage ----------------------------------------------------------------
FROM python:3.12-slim-bookworm
WORKDIR /app
RUN groupadd -r hermes && useradd -r -g hermes hermes
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
RUN pip install --no-cache-dir setuptools
COPY main.py .
COPY test-client.html .
COPY certs/root_ca.crt /etc/hermes-bridge/certs/root_ca.crt
RUN chown -R hermes:hermes /app /etc/hermes-bridge
USER hermes
EXPOSE 8765
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8765/health')" || exit 1
CMD ["python", "main.py"]
