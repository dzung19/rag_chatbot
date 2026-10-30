FROM python:3.12-slim

WORKDIR /app

# Install system dependencies for python-magic, curl, and OpenCV/RapidOCR headless runtime
RUN apt-get update && \
    apt-get install -y --no-install-recommends libmagic1 curl libglib2.0-0 libgl1 libgomp1 && \
    rm -rf /var/lib/apt/lists/*

# Copy and install Python dependencies
COPY services/ingestion/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir --default-timeout=120 --retries 10 -r requirements.txt && \
    pip uninstall -y opencv-python && \
    pip install --no-cache-dir --force-reinstall --no-deps "opencv-python-headless>=4.8.0,<5.0.0"

# Copy shared libraries
COPY shared/ /app/shared/

# Copy service code
COPY services/ingestion/ /app/

# Create upload directory (outside web root, non-executable)
RUN mkdir -p /app/uploaded_docs && \
    chmod 755 /app/uploaded_docs

EXPOSE 8001

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8001", "--workers", "1"]
