from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import os
from app.api.router import api_router

FRONTEND_URL = os.getenv("FRONTEND_URL")  

app = FastAPI(title="AVOCarbon Complaints/8D report API")

origins = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "https://avocarbon-customer-complaint.azurewebsites.net"
]

# Ajoute l'env si elle existe
if FRONTEND_URL:
    origins.append(FRONTEND_URL)

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
