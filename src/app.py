from fastapi import FastAPI
from fastapi.responses import JSONResponse
from sqlalchemy import text

from src.db.session import engine

app = FastAPI(title="Chokepoint", version="0.1.0")


@app.get("/health")
def health() -> JSONResponse:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception:
        return JSONResponse(
            status_code=503,
            content={"status": "degraded", "db": "disconnected"},
        )
    return JSONResponse(
        status_code=200,
        content={"status": "ok", "db": "connected"},
    )
