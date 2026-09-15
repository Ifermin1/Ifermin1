# Etapa 1: consola web
FROM node:22-alpine AS web
WORKDIR /web
COPY web/package*.json ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
RUN npm run build

# Etapa 2: engine
FROM python:3.11-slim
WORKDIR /app/engine
COPY engine/pyproject.toml ./
COPY engine/tradepilot ./tradepilot
RUN pip install --no-cache-dir .
COPY --from=web /web/dist /app/web/dist
ENV WEB_DIST=/app/web/dist API_HOST=0.0.0.0 API_PORT=8000 DB_PATH=/data/tradepilot.db
VOLUME ["/data"]
EXPOSE 8000
CMD ["python", "-m", "tradepilot.main"]
