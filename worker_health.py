"""Worker health check utilities"""

from celery_app import celery_app
from typing import Dict, List
import logging
import asyncio
from functools import wraps

logger = logging.getLogger(__name__)


def async_to_sync_wrapper(func):
    """Decorator to make async functions callable from sync context"""
    @wraps(func)
    def wrapper(*args, **kwargs):
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

        return loop.run_until_complete(func(*args, **kwargs))

    return wrapper


def _check_workers_available_sync(timeout: float = 5.0) -> Dict[str, any]:
    """
    Synchronous implementation of worker availability check
    This should be called from async context using run_in_executor

    Args:
        timeout: Timeout in seconds for the check

    Returns:
        Dict with:
        - available: bool - Whether workers are available
        - worker_count: int - Number of active workers
        - workers: list - List of worker names
        - queues: list - List of available queues
        - error: str - Error message if check failed
    """
    try:
        # Get worker stats with timeout
        inspect = celery_app.control.inspect(timeout=timeout)

        # Check active workers
        stats = inspect.stats()

        if not stats:
            return {
                "available": False,
                "worker_count": 0,
                "workers": [],
                "queues": [],
                "error": "No workers are currently running. Please start the worker service."
            }

        # Get active queues from workers
        active_queues = inspect.active_queues()
        all_queues = set()

        if active_queues:
            for worker_queues in active_queues.values():
                for queue_info in worker_queues:
                    all_queues.add(queue_info['name'])

        worker_names = list(stats.keys())

        return {
            "available": True,
            "worker_count": len(worker_names),
            "workers": worker_names,
            "queues": list(all_queues),
            "error": None
        }

    except Exception as e:
        logger.error(f"Worker health check failed: {str(e)}")
        return {
            "available": False,
            "worker_count": 0,
            "workers": [],
            "queues": [],
            "error": f"Unable to connect to workers: {str(e)}"
        }


async def check_workers_available(timeout: float = 5.0) -> Dict[str, any]:
    """
    Async version: Check if Celery workers are available and healthy
    Runs blocking Celery inspect calls in a thread pool to avoid blocking FastAPI

    Args:
        timeout: Timeout in seconds for the check

    Returns:
        Dict with worker availability information
    """
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _check_workers_available_sync, timeout)


def check_workers_available_sync(timeout: float = 5.0) -> Dict[str, any]:
    """
    Synchronous version for backwards compatibility
    WARNING: This blocks the calling thread - use async version in FastAPI endpoints
    """
    return _check_workers_available_sync(timeout)


def _check_queue_available_sync(queue_name: str, timeout: float = 5.0) -> bool:
    """
    Synchronous implementation: Check if a specific queue has workers listening

    Args:
        queue_name: Name of the queue to check
        timeout: Timeout in seconds

    Returns:
        bool: True if queue has workers, False otherwise
    """
    try:
        inspect = celery_app.control.inspect(timeout=timeout)
        active_queues = inspect.active_queues()

        if not active_queues:
            return False

        # Check if any worker is listening to this queue
        for worker_queues in active_queues.values():
            for queue_info in worker_queues:
                if queue_info['name'] == queue_name:
                    return True

        return False

    except Exception as e:
        logger.error(f"Queue check failed for {queue_name}: {str(e)}")
        return False


async def check_queue_available(queue_name: str, timeout: float = 5.0) -> bool:
    """
    Async version: Check if a specific queue has workers listening
    Runs in thread pool to avoid blocking

    Args:
        queue_name: Name of the queue to check
        timeout: Timeout in seconds

    Returns:
        bool: True if queue has workers, False otherwise
    """
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _check_queue_available_sync, queue_name, timeout)


def check_queue_available_sync(queue_name: str, timeout: float = 5.0) -> bool:
    """Synchronous version for backwards compatibility"""
    return _check_queue_available_sync(queue_name, timeout)


def _get_queue_length_sync(queue_name: str, timeout: float = 3.0) -> int:
    """
    Synchronous implementation: Get the number of tasks waiting in a queue

    Args:
        queue_name: Name of the queue
        timeout: Timeout in seconds for broker connection

    Returns:
        int: Number of tasks in queue, -1 if unable to check
    """
    try:
        # This requires access to the broker (RabbitMQ)
        # For more detailed queue stats, you'd need to query RabbitMQ directly
        inspect = celery_app.control.inspect(timeout=timeout)
        scheduled = inspect.scheduled()
        reserved = inspect.reserved()

        # Count tasks for this queue
        count = 0

        if scheduled:
            for tasks in scheduled.values():
                count += len([t for t in tasks if t.get('delivery_info', {}).get('routing_key') == queue_name])

        if reserved:
            for tasks in reserved.values():
                count += len([t for t in tasks if t.get('delivery_info', {}).get('routing_key') == queue_name])

        return count

    except Exception as e:
        logger.error(f"Failed to get queue length for {queue_name}: {str(e)}")
        return -1


async def get_queue_length(queue_name: str, timeout: float = 3.0) -> int:
    """
    Async version: Get the number of tasks waiting in a queue
    Runs in thread pool to avoid blocking

    Args:
        queue_name: Name of the queue
        timeout: Timeout in seconds for broker connection

    Returns:
        int: Number of tasks in queue, -1 if unable to check
    """
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _get_queue_length_sync, queue_name, timeout)


def get_queue_length_sync(queue_name: str, timeout: float = 3.0) -> int:
    """Synchronous version for backwards compatibility"""
    return _get_queue_length_sync(queue_name, timeout)


async def get_worker_health_summary(timeout: float = 3.0) -> Dict[str, any]:
    """
    Get comprehensive worker health summary (ASYNC version)
    All blocking calls run in thread pool

    Args:
        timeout: Timeout in seconds for all health checks

    Returns:
        Dict with worker health information
    """
    # Run all checks concurrently
    health = await check_workers_available(timeout=timeout)

    # Add queue-specific checks
    audio_queue_ok = await check_queue_available('audio_processing', timeout=timeout)
    maintenance_queue_ok = await check_queue_available('maintenance', timeout=timeout)

    return {
        **health,
        "audio_processing_queue_ok": audio_queue_ok,
        "maintenance_queue_ok": maintenance_queue_ok,
        "audio_queue_length": await get_queue_length('audio_processing', timeout=timeout) if audio_queue_ok else -1,
        "status": "healthy" if health["available"] and audio_queue_ok else "unhealthy",
        "message": health["error"] if not health["available"] else "Workers are operational"
    }


def get_worker_health_summary_sync(timeout: float = 3.0) -> Dict[str, any]:
    """
    Synchronous version for backwards compatibility
    WARNING: This blocks - use async version in FastAPI endpoints
    """
    health = _check_workers_available_sync(timeout=timeout)
    audio_queue_ok = _check_queue_available_sync('audio_processing', timeout=timeout)
    maintenance_queue_ok = _check_queue_available_sync('maintenance', timeout=timeout)

    return {
        **health,
        "audio_processing_queue_ok": audio_queue_ok,
        "maintenance_queue_ok": maintenance_queue_ok,
        "audio_queue_length": _get_queue_length_sync('audio_processing', timeout=timeout) if audio_queue_ok else -1,
        "status": "healthy" if health["available"] and audio_queue_ok else "unhealthy",
        "message": health["error"] if not health["available"] else "Workers are operational"
    }
