FROM python:3.12-alpine

COPY . /app/
WORKDIR /app

RUN apk update && apk add gcc musl-dev mariadb-connector-c  mariadb-dev && pip install mariadb 
RUN pip cache purge && apk del --rdepends --purge musl-dev gcc mariadb-dev

RUN pip install -r requirements.txt

CMD python /app/app/main.py