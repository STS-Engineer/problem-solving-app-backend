from fastapi import FastAPI

from app.api.router import api_router

app = FastAPI(title="AVOCarbon Complaints/8D report API")

app.include_router(api_router, prefix="/api/v1")


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}
