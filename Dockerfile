# Multi-stage build for Forecast Service

FROM python:3.11-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential gcc gfortran \
    libpango-1.0-0 libpangoft2-1.0-0 libffi-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt /app/requirements.txt
RUN pip install --upgrade pip && pip install -r requirements.txt

COPY . /app
ENV PORT=8000

CMD ["gunicorn", "-w", "4", "-b", "0.0.0.0:8000", "app:app"]



