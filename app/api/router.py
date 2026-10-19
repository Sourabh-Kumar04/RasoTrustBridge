from fastapi import APIRouter
from app.api.contracts import router as contracts_router
from app.api.milestones import router as milestones_router
from app.api.ai import router as ai_router
from app.api.fx import router as fx_router
from app.api.paypal import router as paypal_router
from app.api.negotiate import router as negotiate_router
from app.api.webhooks import router as webhooks_router

api_router = APIRouter(prefix="/api/v1")

api_router.include_router(contracts_router)
api_router.include_router(milestones_router)
api_router.include_router(ai_router)
api_router.include_router(fx_router)
api_router.include_router(paypal_router)
api_router.include_router(negotiate_router)
api_router.include_router(webhooks_router)
