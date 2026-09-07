FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 TZ=Asia/Shanghai
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates tzdata \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 marketdata
WORKDIR /app
COPY market_data_service/requirements.lock /app/requirements.lock
RUN pip install --no-cache-dir -r /app/requirements.lock
COPY --chown=marketdata:marketdata market_data_service /app/market_data_service
USER marketdata
EXPOSE 8010
CMD ["uvicorn", "market_data_service.api:app", "--host", "0.0.0.0", "--port", "8010"]
