# services/pricing_engine/Dockerfile
FROM python:3.11-slim AS builder
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

FROM python:3.11-slim
WORKDIR /app
COPY --from=builder /root/.local /root/.local
COPY . /app/services/pricing_engine
COPY shared /app/shared
ENV PATH=/root/.local/bin:$PATH
ENV PYTHONPATH=/app
CMD ["uvicorn", "services.pricing_engine.app.main:app", "--host", "0.0.0.0", "--port", "8002"]
