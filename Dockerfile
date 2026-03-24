FROM python:3.12-slim AS builder

WORKDIR /app

COPY requirements.txt .

RUN apt-get update && apt-get install -y --no-install-recommends \
    libnss3 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 \
    libxkbcommon0 libxcomposite1 libxdamage1 libxrandr2 libgbm1 \
    libpango-1.0-0 libcairo2 libasound2t64 libxshmfence1 \
    libpangocairo-1.0-0 libgdk-pixbuf-2.0-0 libffi-dev \
    gcc libpq-dev \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir -r requirements.txt
RUN playwright install chromium --with-deps

# ── runtime stage ──────────────────────────────────────────────────────────────
FROM python:3.12-slim

WORKDIR /app

# Copy Python packages + Playwright browser binary from builder
COPY --from=builder /usr/local/bin /usr/local/bin
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /root/.cache/ms-playwright /root/.cache/ms-playwright

# Let Playwright install ALL system deps it needs — no more guessing
RUN playwright install-deps chromium

# Extra runtime deps: WeasyPrint, Postgres client, fonts
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpango-1.0-0 libpangocairo-1.0-0 libgdk-pixbuf-2.0-0 \
    libcairo2 libffi8ubuntu1 \
    libpq5 fonts-liberation fonts-noto-core \
    && rm -rf /var/lib/apt/lists/*

COPY scanner/ scanner/
COPY templates/ templates/
COPY tools/ tools/
COPY api.py main.py config.yml requirements.txt run.sh ./

RUN mkdir -p runs/reports

ENV PYTHONUNBUFFERED=1
ENV PORT=8000

EXPOSE 8000

CMD ["uvicorn", "api:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]