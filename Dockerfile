FROM python:3.11-slim

COPY . /app/
WORKDIR /app

RUN apt update && apt install -y python3-pip libmariadb-dev ffmpeg && rm -rf /var/lib/apt/lists/*

RUN pip install -r requirements.txt

CMD python /app/app/main.py