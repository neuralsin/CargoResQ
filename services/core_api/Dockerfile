# Root-context image: the compose file builds this Dockerfile with the
# repository root as context, so all shared services resolve consistently.
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV PYTHONPATH=/app
ENV PYTHONDONTWRITEBYTECODE=1
RUN useradd --create-home --uid 10001 cargoresq
USER cargoresq
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
