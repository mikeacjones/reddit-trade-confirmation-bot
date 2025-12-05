# syntax=docker/dockerfile:1
FROM python:3.11.8-alpine
WORKDIR /app
RUN apk update && apk add git
COPY ./src/* .
RUN pip install -r requirements.txt
COPY .env .
CMD ["python3", "bot.py"]