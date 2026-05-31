FROM python:3.12-slim

WORKDIR /app

# Install dependencies before copying app code for better layer caching.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code (test/dev files excluded via .dockerignore).
COPY . .

# Unbuffered output so logs appear immediately in Cloud Run.
ENV PYTHONUNBUFFERED=1
ENV PORT=8080

CMD uvicorn main:app --host 0.0.0.0 --port ${PORT}
