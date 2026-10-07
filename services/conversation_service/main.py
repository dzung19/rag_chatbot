from __future__ import annotations

from fastapi import FastAPI
from sqlalchemy import text

from services.conversation_service.database import (
    SessionLocal,
)
from services.conversation_service.router import (
    internal_router,
    public_router,
)


app = FastAPI(
    title="Conversation Service",
    version="0.1.0",
)

app.include_router(public_router)
app.include_router(internal_router)


@app.get("/health")
def health():
    with SessionLocal() as session:
        session.execute(
            text("SELECT 1")
        )

    return {
        "service":
            "conversation",
        "status":
            "healthy",
        "version":
            "0.1.0",
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app,
        host="127.0.0.1",
        port=8005,
    )