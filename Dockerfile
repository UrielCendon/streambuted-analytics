# syntax=docker/dockerfile:1

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY services/analytics-service/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY services/analytics-service/app ./app

RUN addgroup --system appgroup && adduser --system --ingroup appgroup appuser

EXPOSE 8085

USER appuser
CMD ["python", "-m", "app.server"]
