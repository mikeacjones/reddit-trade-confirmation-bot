# syntax=docker/dockerfile:1
FROM python:3.11.8-alpine
COPY ./src/* . 
RUN apk update
RUN apk add git
RUN pip install -r requirements.txt
COPY . .
CMD ["python3", "bot.py"]