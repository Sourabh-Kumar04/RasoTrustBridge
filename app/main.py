from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.config import get_settings
from app.database import init_db
from app.api.router import api_router

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialize database tables on startup
    init_db()
    yield


app = FastAPI(
    title="TrustBridge Core API",
    description=(
        "Programmable trust layer for international freelancers. "
        "Transforms plain-English briefs into structured milestones with verifiable acceptance criteria, "
        "holds escrow funds via PayPal Orders API, verifies code deliverables, "
        "and programmatically executes cross-border releases with automated FX risk protection."
    ),
    version=settings.VERSION,
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

# Cross-Origin Resource Sharing
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include API v1 router
app.include_router(api_router)


@app.get("/", tags=["Health"])
def root():
    return {
        "service": "TrustBridge",
        "description": "Programmable Trust Layer for International Freelancers",
        "version": settings.VERSION,
        "environment": settings.ENVIRONMENT,
        "paypal_mode": settings.PAYPAL_MODE,
        "docs_url": "/docs",
        "status": "operational",
    }


@app.get("/health", tags=["Health"])
def health_check():
    return {
        "status": "healthy",
        "engine": "FastAPI",
        "database": "sqlite_wal" if "sqlite" in settings.DATABASE_URL else "postgresql",
        "paypal_configured": bool(settings.PAYPAL_CLIENT_ID and len(settings.PAYPAL_CLIENT_ID) > 5),
    }
