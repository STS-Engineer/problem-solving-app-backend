from fastapi import APIRouter
from app.api.routes.complaints import router as complaints_router



api_router = APIRouter()

api_router.include_router(complaints_router, prefix="/complaints", tags=["complaints"])

