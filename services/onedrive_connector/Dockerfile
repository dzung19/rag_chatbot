FROM python:3.12-slim

WORKDIR /app

COPY services/onedrive_connector/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy shared libraries
COPY shared/ /app/shared/

# Copy service code
COPY services/onedrive_connector/ /app/

EXPOSE 8004

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8004", "--workers", "1"]
