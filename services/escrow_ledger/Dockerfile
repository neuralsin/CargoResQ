# services/escrow_ledger/Dockerfile
FROM python:3.11-slim AS builder
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

FROM python:3.11-slim
WORKDIR /app
COPY --from=builder /root/.local /root/.local
COPY . /app/services/escrow_ledger
COPY shared /app/shared
ENV PATH=/root/.local/bin:$PATH
ENV PYTHONPATH=/app
CMD ["uvicorn", "services.escrow_ledger.app.main:app", "--host", "0.0.0.0", "--port", "8003"]
