FROM python:3.12-slim

WORKDIR /app

# ── Python deps first (cached layer) ──────────────────────────────────────────
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ── Playwright: download Chromium + install ALL system deps in one shot ────────
# Single command = browser + libs in the same layer = no missing lib possible
RUN playwright install --with-deps chromium

# ── Extra deps: WeasyPrint rendering, Postgres client, fonts ──────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpango-1.0-0 libpangocairo-1.0-0 libgdk-pixbuf-2.0-0 \
    libcairo2 libffi-dev \
    libpq5 fonts-liberation fonts-noto-core \
    && rm -rf /var/lib/apt/lists/*

# ── Application code ──────────────────────────────────────────────────────────
COPY scanner/ scanner/
COPY templates/ templates/
COPY tools/ tools/
COPY api.py main.py config.yml requirements.txt run.sh ./

RUN mkdir -p runs/reports

ENV PYTHONUNBUFFERED=1
ENV PORT=8000
EXPOSE 8000

CMD ["uvicorn", "api:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]