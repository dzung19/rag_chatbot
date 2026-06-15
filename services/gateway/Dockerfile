FROM python:3.12-slim

WORKDIR /app

COPY services/gateway/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy shared libraries
COPY shared/ /app/shared/

# Copy service code
COPY services/gateway/ /app/

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
