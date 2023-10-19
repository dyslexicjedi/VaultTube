FROM ubuntu:22.04

COPY . /app/
WORKDIR /app

RUN apt update && apt install -y cron curl python3-pip libmariadb-dev && rm -rf /var/lib/apt/lists/*
RUN ln -s /usr/bin/python3 /usr/bin/python

RUN pip3 install -r requirements.txt

CMD python3 /app/app/main.py