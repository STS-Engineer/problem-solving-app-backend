from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import os
import logging

from app.api.router import api_router
from app.services.scheduler import start_scheduler, stop_scheduler
  

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
# ── CORS ──────────────────────────────────────────────────────────────────────
FRONTEND_URL = os.getenv("FRONTEND_URL")

origins = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "https://avocarbon-customer-complaint.azurewebsites.net",
]
if FRONTEND_URL:
    origins.append(FRONTEND_URL)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:   # ← `app` added
    logger.info("🚀 Starting application...")
    start_scheduler()
    yield
    stop_scheduler()
    logger.info("🛑 Application stopped.")


# ── App ───────────────────────────────────────────────────────────────────────
app = FastAPI(
    title="AVOCarbon Complaints/8D report API",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router, prefix="/api/v1")


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}