# Stage 1: Build the frontend
FROM node:20-slim AS frontend
WORKDIR /frontend
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ .
RUN npm run build

# Stage 2: Fetch the litestream binary (see deploy-gcp.md for why it's here).
FROM debian:bookworm-slim AS litestream
ARG LITESTREAM_VERSION=0.5.15
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*
RUN curl -fsSL -o /tmp/litestream.tar.gz \
      "https://github.com/benbjohnson/litestream/releases/download/v${LITESTREAM_VERSION}/litestream-${LITESTREAM_VERSION}-linux-x86_64.tar.gz" \
    && tar -xzf /tmp/litestream.tar.gz -C /usr/local/bin litestream

# Stage 3: Backend + bundled frontend + litestream
FROM python:3.12-slim
WORKDIR /app

COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && \
    pip install --no-cache-dir --no-deps "withings-api>=2.4.0"

COPY --from=litestream /usr/local/bin/litestream /usr/local/bin/litestream
COPY backend/ .
COPY --from=frontend /frontend/dist ./static
COPY litestream.yml /etc/litestream.yml
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

# The live database lives on local (ephemeral) disk under litestream, not on
# the GCS-FUSE volume mount -- see deploy-gcp.md's "Database storage" section.
RUN mkdir -p /app/db

ENV PYTHONUNBUFFERED=1
ENV PORT=8080

CMD ["/usr/local/bin/docker-entrypoint.sh"]
