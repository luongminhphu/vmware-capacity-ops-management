# Build context MUST be the repository root (see docker-compose.yml `build.context: ..`)
# so this Dockerfile can COPY both backend/ and frontend/ into the image.
FROM python:3.12-slim

WORKDIR /app

# psycopg2-binary needs libpq at runtime; curl is used by the healthcheck.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/app ./app
# app/main.py computes FRONTEND_DIR as three parents above main.py itself
# (i.e. Path(__file__).parent.parent.parent). With this image's layout
# (/app/app/main.py) that resolves to "/frontend" — copy it there so FastAPI
# can serve it as static files.
COPY frontend ../frontend

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -f http://localhost:8000/api/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
