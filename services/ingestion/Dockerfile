FROM python:3.12-slim

WORKDIR /app

# Install system dependencies for python-magic
RUN apt-get update && \
    apt-get install -y --no-install-recommends libmagic1 && \
    rm -rf /var/lib/apt/lists/*

# Copy and install Python dependencies
COPY services/ingestion/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy shared libraries
COPY shared/ /app/shared/

# Copy service code
COPY services/ingestion/ /app/

# Create upload directory (outside web root, non-executable)
RUN mkdir -p /app/uploaded_docs && \
    chmod 755 /app/uploaded_docs

EXPOSE 8001

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8001", "--workers", "2"]
