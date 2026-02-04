"""Shared Celery app instance for the backend service"""

from celery import Celery
from wazz_shared.config import get_shared_settings

settings = get_shared_settings()

celery_app = Celery(
    'whazz_audio_worker',
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
)
