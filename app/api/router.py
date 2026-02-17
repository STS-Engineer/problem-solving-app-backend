from fastapi import APIRouter
from app.api.routes.complaints import router as complaints_router
from app.api.routes.steps import router as steps_router
from app.api.routes.reports import router as reports_router
from app.api.routes.chatbot import router as chatbot_router
from app.api.routes.dashboard import router as dashboard_router
from app.api.routes.step_files import router as step_files_router

api_router = APIRouter()

api_router.include_router(complaints_router, prefix="/complaints", tags=["complaints"])
api_router.include_router(steps_router, prefix="/steps", tags=["steps"])
api_router.include_router(reports_router, prefix="/reports", tags=["reports"])
api_router.include_router(chatbot_router, prefix="/chatbot", tags=["🤖 AI Chatbot Coach"])
api_router.include_router(dashboard_router, prefix="/dashboard", tags=["📊 Dashboard"])  # NEW
api_router.include_router(step_files_router,prefix="/steps", tags=["steps"])
