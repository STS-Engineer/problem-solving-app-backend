from fastapi import APIRouter
from app.api.routes.complaints import router as complaints_router
from app.api.routes.steps import router as steps_router
from app.api.routes.reports import router as reports_router
from app.api.routes.dashboard import router as dashboard_router
from app.api.routes.step_files import router as step_files_router
from app.api.routes.conversation import router as conversation_router
from app.api.routes.logger_complaint import router as logger_complaint_router
from app.api.routes.debug_escalation import router as debug_router
from app.api.routes.test_members import router as test_members_router
<<<<<<< Updated upstream


=======
from app.api.routes.audit_priorities import router as priorities_router
from app.api.routes.admin_router import router as admin_router
>>>>>>> Stashed changes

api_router = APIRouter()

api_router.include_router(complaints_router, prefix="/complaints", tags=["complaints"])
api_router.include_router(
    logger_complaint_router, prefix="/logger", tags=["complaint-logger"]
)
api_router.include_router(conversation_router, prefix="/steps", tags=["conversations"])
api_router.include_router(steps_router, prefix="/steps", tags=["steps"])
api_router.include_router(reports_router, prefix="/reports", tags=["reports"])
api_router.include_router(
    dashboard_router, prefix="/dashboard", tags=["📊 Dashboard"]
)  # NEW
api_router.include_router(step_files_router, prefix="/steps", tags=["steps"])
api_router.include_router(
    test_members_router, prefix="/test-members", tags=["test-members"]
)
api_router.include_router(debug_router)
<<<<<<< Updated upstream
=======
api_router.include_router(priorities_router, prefix="/complaints", tags=["Audit"])
api_router.include_router(admin_router, prefix="/admin", tags=["admin"])
>>>>>>> Stashed changes
