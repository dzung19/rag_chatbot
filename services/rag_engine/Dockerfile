FROM python:3.12-slim

WORKDIR /app

# curl is used by the container healthcheck
RUN apt-get update && \
    apt-get install -y --no-install-recommends curl && \
    rm -rf /var/lib/apt/lists/*

COPY services/rag_engine/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir --default-timeout=120 --retries 10 -r requirements.txt

# Copy shared libraries
COPY shared/ /app/shared/

# Copy service code
COPY services/rag_engine/ /app/

EXPOSE 8002

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8002", "--workers", "2"]
