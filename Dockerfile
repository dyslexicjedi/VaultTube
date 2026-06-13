FROM python:3.12-slim

# Expensive layers first so code changes don't invalidate them
RUN apt update && apt install -y --no-install-recommends \
      libmariadb-dev gcc ffmpeg curl unzip \
    && rm -rf /var/lib/apt/lists/*

# Install Deno — pass -y to suppress any interactive prompts
ENV DENO_INSTALL="/root/.deno"
ENV PATH="${DENO_INSTALL}/bin:${PATH}"
RUN curl -fsSL https://deno.land/install.sh | sh -s -- -y

COPY requirements.txt /app/requirements.txt
WORKDIR /app
RUN pip install -r requirements.txt

COPY . /app/

# Shell form so ${VAULTTUBE_PORT} expands; /api/health is one SELECT 1
HEALTHCHECK --interval=60s --timeout=10s --start-period=30s --retries=3 \
  CMD curl -fsS "http://localhost:${VAULTTUBE_PORT:-5000}/api/health" || exit 1

# Exec form: python runs as PID 1 and receives docker stop's SIGTERM directly
# (shell form wraps it in /bin/sh, which swallows the signal as PID 1)
CMD ["python", "/app/app/main.py"]
