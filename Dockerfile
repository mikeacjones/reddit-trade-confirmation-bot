# syntax=docker/dockerfile:1
FROM golang:1.26-alpine AS build
WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
COPY . .
RUN CGO_ENABLED=0 go build -o /reddit-bot ./cmd/bot

FROM alpine:3.21
RUN apk add --no-cache ca-certificates
WORKDIR /app
COPY --from=build /reddit-bot /app/reddit-bot
COPY mdtemplates/ /app/mdtemplates/
COPY entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

ENV TEMPORAL_HOST=host.docker.internal:7233
ENTRYPOINT ["/app/entrypoint.sh"]
