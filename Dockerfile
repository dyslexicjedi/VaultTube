FROM python:3.11-slim

COPY . /app/
WORKDIR /app

RUN apt update && apt install -y python3-pip libmariadb-dev ffmpeg curl unzip 

RUN curl -fsSL https://deno.land/install.sh | sh

RUN curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.3/install.sh | bash && \. "$HOME/.nvm/nvm.sh" && nvm install 24

RUN rm -rf /var/lib/apt/lists/*

RUN pip install -r requirements.txt

CMD python /app/app/main.py