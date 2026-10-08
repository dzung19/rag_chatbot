FROM python:3.12-slim

WORKDIR /app

# Install curl for container healthchecks
RUN apt-get update && \
    apt-get install -y --no-install-recommends curl && \
    rm -rf /var/lib/apt/lists/*

COPY services/conversation_service/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy alembic migration files
COPY alembic.ini /app/alembic.ini
COPY alembic/ /app/alembic/

# Copy shared libraries
COPY shared/ /app/shared/

# Copy service code
COPY services/conversation_service/ /app/services/conversation_service/

ENV PYTHONPATH=/app

EXPOSE 8005

CMD ["sh", "-c", "alembic upgrade head && uvicorn services.conversation_service.main:app --host 0.0.0.0 --port 8005"]
