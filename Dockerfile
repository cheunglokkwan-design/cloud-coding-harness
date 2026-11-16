# Orchestrator + control plane in one image.
FROM node:22-slim AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
RUN npm run build

FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY orchestrator/ orchestrator/
RUN pip install --no-cache-dir ./orchestrator
COPY examples/ examples/
COPY --from=web /web/dist web/dist
ENV HARNESS_WEB_DIST=/app/web/dist \
    HARNESS_HOST=0.0.0.0 \
    HARNESS_PORT=8080
EXPOSE 8080
CMD ["harness", "serve"]
