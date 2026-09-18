# services/matching_engine/Dockerfile
FROM python:3.11-slim AS builder
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

FROM python:3.11-slim
WORKDIR /app
COPY --from=builder /root/.local /root/.local
COPY . /app/services/matching_engine
COPY shared /app/shared
ENV PATH=/root/.local/bin:$PATH
ENV PYTHONPATH=/app
CMD ["uvicorn", "services.matching_engine.app.main:app", "--host", "0.0.0.0", "--port", "8001"]
