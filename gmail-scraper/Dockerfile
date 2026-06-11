FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

HEALTHCHECK --interval=60s --timeout=5s --retries=3 --start-period=30s \
    CMD ["/bin/sh", "/app/healthcheck.sh"]

CMD ["python", "main.py"]
