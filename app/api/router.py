from fastapi import APIRouter
from app.api.routes.complaints import router as complaints_router
from app.api.routes.steps import router as steps_router
from app.api.routes.reports import router as reports_router

api_router = APIRouter()

api_router.include_router(complaints_router, prefix="/complaints", tags=["complaints"])
api_router.include_router(steps_router, prefix="/steps", tags=["steps"])
api_router.include_router(reports_router, prefix="/reports", tags=["reports"])

