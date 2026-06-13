FROM python:3.12-slim

COPY . /app/
WORKDIR /app

RUN apt update && apt install -y python3-pip libmariadb-dev ffmpeg curl unzip

# Install Deno — pass -y to suppress any interactive prompts
ENV DENO_INSTALL="/root/.deno"
ENV PATH="${DENO_INSTALL}/bin:${PATH}"
RUN curl -fsSL https://deno.land/install.sh | sh -s -- -y

RUN rm -rf /var/lib/apt/lists/*

RUN pip install -r requirements.txt

# Shell form so ${VAULTTUBE_PORT} expands; /api/health is one SELECT 1
HEALTHCHECK --interval=60s --timeout=10s --start-period=30s --retries=3 \
  CMD curl -fsS "http://localhost:${VAULTTUBE_PORT:-5000}/api/health" || exit 1

# Exec form: python runs as PID 1 and receives docker stop's SIGTERM directly
# (shell form wraps it in /bin/sh, which swallows the signal as PID 1)
CMD ["python", "/app/app/main.py"]