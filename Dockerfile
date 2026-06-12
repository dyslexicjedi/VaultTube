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

# Exec form: python runs as PID 1 and receives docker stop's SIGTERM directly
# (shell form wraps it in /bin/sh, which swallows the signal as PID 1)
CMD ["python", "/app/app/main.py"]