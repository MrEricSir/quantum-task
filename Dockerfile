# Stage 1: Build the frontend
FROM node:20-slim AS frontend
WORKDIR /frontend
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ .
RUN npm run build

# Stage 2: Backend + bundled frontend
FROM python:3.12-slim
WORKDIR /app

COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && \
    pip install --no-cache-dir --no-deps "withings-api>=2.4.0"

COPY backend/ .
COPY --from=frontend /frontend/dist ./static

ENV PYTHONUNBUFFERED=1
ENV PORT=8080

CMD uvicorn main:app --host 0.0.0.0 --port ${PORT}
