FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc curl \
    libmupdf-dev \
    mupdf-tools \
    && rm -rf /var/lib/apt/lists/*

COPY bill-parser/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Bake Chart.js into the image so dashboard.html is self-contained
# (CDN scripts are blocked by Telegram's in-app HTML viewer).
RUN curl -sLfo /app/chart.umd.min.js \
    https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js

COPY bill-parser/ .
COPY shared/ ./shared/

HEALTHCHECK --interval=30s --timeout=5s --retries=3 --start-period=10s \
    CMD curl -sf http://localhost:8001/health || exit 1

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8001"]
