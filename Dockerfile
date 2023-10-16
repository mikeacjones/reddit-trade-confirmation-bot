# syntax=docker/dockerfile:1
FROM python:3.11.6-alpine
COPY ./src/* . 
RUN pip install -r requirements.txt
COPY . .
CMD ["python3", "bot.py"]