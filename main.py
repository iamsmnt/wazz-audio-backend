"""Main FastAPI application"""

import warnings
# Suppress bcrypt version warning (known passlib 1.7.4 + bcrypt 4.x compatibility issue)
warnings.filterwarnings("ignore", message=".*error reading bcrypt version.*")

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from wazz_shared.config import get_shared_settings
from wazz_shared.database import engine, Base
from celery import Celery
from routers import auth, guest, audio, usage_stats, admin, user_settings

settings = get_shared_settings()
celery_app = Celery(broker=settings.celery_broker_url, backend=settings.celery_result_backend)

# Create database tables
Base.metadata.create_all(bind=engine)

# Initialize FastAPI app
app = FastAPI(
    title=settings.app_name,
    description="Authentication API for Whazz Audio application",
    version="1.0.0",
    debug=settings.debug,
)

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(auth.router)
app.include_router(guest.router)
app.include_router(audio.router)
app.include_router(usage_stats.router)
app.include_router(admin.router)
app.include_router(user_settings.router)


@app.get("/")
def root():
    """Root endpoint"""
    return {
        "message": "Welcome to Whazz Audio Authentication API",
        "version": "1.0.0",
        "docs": "/docs",
    }


@app.get("/health")
def health_check():
    """
    Health check endpoint

    Returns the health status of the API and worker services.
    This endpoint is useful for monitoring and load balancers.
    """
    # Check worker health via Celery inspect
    worker_info = {"available": False, "worker_count": 0, "queues": [], "error": None}
    try:
        inspect = celery_app.control.inspect(timeout=2.0)
        active_workers = inspect.active_queues() or {}
        worker_count = len(active_workers)
        queues = []
        for worker_queues in active_workers.values():
            queues.extend(q["name"] for q in worker_queues)
        worker_info = {"available": worker_count > 0, "worker_count": worker_count, "queues": list(set(queues)), "error": None}
    except Exception as e:
        worker_info = {"available": False, "worker_count": 0, "queues": [], "error": str(e)}

    return {
        "status": "healthy",
        "service": settings.app_name,
        "api": {
            "status": "operational",
            "version": "1.0.0"
        },
        "workers": {
            "status": "operational" if worker_info["available"] else "unavailable",
            "available": worker_info["available"],
            "count": worker_info["worker_count"],
            "queues": worker_info["queues"] if worker_info["available"] else [],
            "warning": worker_info["error"] if not worker_info["available"] else None
        }
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
