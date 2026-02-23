FROM python:3.12-slim

COPY . /app/
WORKDIR /app

RUN apt update && apt install -y python3-pip libmariadb-dev ffmpeg curl unzip

# Install Deno — pass -y to suppress any interactive prompts
ENV DENO_INSTALL="/root/.deno"
ENV PATH="${DENO_INSTALL}/bin:${PATH}"
RUN curl -fsSL https://deno.land/install.sh | sh -s -- -y

# Install Node via NVM — source nvm.sh and symlink node/npm into PATH
ENV NVM_DIR="/root/.nvm"
RUN curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.3/install.sh | bash \
    && . "$NVM_DIR/nvm.sh" \
    && nvm install 24 \
    && nvm use 24 \
    && ln -s $(which node) /usr/local/bin/node \
    && ln -s $(which npm) /usr/local/bin/npm

RUN rm -rf /var/lib/apt/lists/*

RUN pip install -r requirements.txt

CMD python /app/app/main.py