FROM python:3.12-slim

WORKDIR /app

COPY services/rag_engine/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy shared libraries
COPY shared/ /app/shared/

# Copy service code
COPY services/rag_engine/ /app/

EXPOSE 8002

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8002", "--workers", "2"]
