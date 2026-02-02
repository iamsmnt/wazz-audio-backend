"""Usage tracking service for monitoring user and guest activity"""

from datetime import datetime
from sqlalchemy.orm import Session
from wazz_shared.models import UserUsageStats
import os


def get_or_create_usage_stats(db: Session, user_id: int = None, guest_id: str = None) -> UserUsageStats:
    """
    Get existing usage stats or create new ones for a user/guest

    Args:
        db: Database session
        user_id: User ID (for authenticated users)
        guest_id: Guest ID (for guest users)

    Returns:
        UserUsageStats object
    """
    if user_id:
        stats = db.query(UserUsageStats).filter(UserUsageStats.user_id == user_id).first()
        if not stats:
            stats = UserUsageStats(user_id=user_id)
            db.add(stats)
            db.commit()
            db.refresh(stats)
    elif guest_id:
        stats = db.query(UserUsageStats).filter(UserUsageStats.guest_id == guest_id).first()
        if not stats:
            stats = UserUsageStats(guest_id=guest_id)
            db.add(stats)
            db.commit()
            db.refresh(stats)
    else:
        raise ValueError("Either user_id or guest_id must be provided")

    return stats


def track_file_upload(
    db: Session,
    user_id: int = None,
    guest_id: str = None,
    file_size: float = 0.0,
    processing_type: str = None
):
    """
    Track when a user uploads a file

    Args:
        db: Database session
        user_id: User ID (optional)
        guest_id: Guest ID (optional)
        file_size: Size of uploaded file in bytes
        processing_type: Type of processing requested
    """
    stats = get_or_create_usage_stats(db, user_id, guest_id)

    # Update statistics
    stats.total_files_uploaded += 1
    stats.total_input_size += file_size

    # Track processing type
    if processing_type:
        if stats.processing_types_count is None:
            stats.processing_types_count = {}

        current_count = stats.processing_types_count.get(processing_type, 0)
        stats.processing_types_count[processing_type] = current_count + 1

    # Update timestamps
    if not stats.first_upload_at:
        stats.first_upload_at = datetime.utcnow()
    stats.last_upload_at = datetime.utcnow()

    db.commit()


def track_processing_complete(
    db: Session,
    user_id: int = None,
    guest_id: str = None,
    processing_time: float = 0.0,
    output_file_size: float = 0.0,
    success: bool = True
):
    """
    Track when processing completes (success or failure)

    Args:
        db: Database session
        user_id: User ID (optional)
        guest_id: Guest ID (optional)
        processing_time: Time taken to process in seconds
        output_file_size: Size of output file in bytes
        success: Whether processing succeeded
    """
    stats = get_or_create_usage_stats(db, user_id, guest_id)

    if success:
        stats.total_files_processed += 1
        stats.total_output_size += output_file_size
    else:
        stats.total_files_failed += 1

    stats.total_processing_time += processing_time

    db.commit()


def track_file_download(
    db: Session,
    user_id: int = None,
    guest_id: str = None
):
    """
    Track when a user downloads a processed file

    Args:
        db: Database session
        user_id: User ID (optional)
        guest_id: Guest ID (optional)
    """
    stats = get_or_create_usage_stats(db, user_id, guest_id)

    stats.total_files_downloaded += 1
    stats.last_download_at = datetime.utcnow()

    db.commit()


def track_api_call(
    db: Session,
    user_id: int = None,
    guest_id: str = None
):
    """
    Track API calls for rate limiting or analytics

    Args:
        db: Database session
        user_id: User ID (optional)
        guest_id: Guest ID (optional)
    """
    stats = get_or_create_usage_stats(db, user_id, guest_id)

    stats.api_calls_count += 1
    stats.last_api_call_at = datetime.utcnow()

    db.commit()


def get_usage_stats(db: Session, user_id: int = None, guest_id: str = None) -> dict:
    """
    Get usage statistics for a user or guest

    Args:
        db: Database session
        user_id: User ID (optional)
        guest_id: Guest ID (optional)

    Returns:
        Dictionary with usage statistics
    """
    stats = get_or_create_usage_stats(db, user_id, guest_id)

    # Calculate derived metrics
    avg_processing_time = (
        stats.total_processing_time / stats.total_files_processed
        if stats.total_files_processed > 0 else 0
    )

    success_rate = (
        (stats.total_files_processed / (stats.total_files_processed + stats.total_files_failed) * 100)
        if (stats.total_files_processed + stats.total_files_failed) > 0 else 0
    )

    avg_file_size = (
        stats.total_input_size / stats.total_files_uploaded
        if stats.total_files_uploaded > 0 else 0
    )

    return {
        # Basic counts
        "total_files_uploaded": stats.total_files_uploaded,
        "total_files_processed": stats.total_files_processed,
        "total_files_failed": stats.total_files_failed,
        "total_files_downloaded": stats.total_files_downloaded,

        # Storage (convert bytes to MB for readability)
        "total_input_size_mb": round(stats.total_input_size / (1024 * 1024), 2),
        "total_output_size_mb": round(stats.total_output_size / (1024 * 1024), 2),
        "average_file_size_mb": round(avg_file_size / (1024 * 1024), 2),

        # Processing time
        "total_processing_time_minutes": round(stats.total_processing_time / 60, 2),
        "average_processing_time_seconds": round(avg_processing_time, 2),

        # Processing types breakdown
        "processing_types_breakdown": stats.processing_types_count or {},

        # Derived metrics
        "success_rate_percent": round(success_rate, 2),

        # Activity timestamps
        "first_upload_at": stats.first_upload_at.isoformat() if stats.first_upload_at else None,
        "last_upload_at": stats.last_upload_at.isoformat() if stats.last_upload_at else None,
        "last_download_at": stats.last_download_at.isoformat() if stats.last_download_at else None,

        # API usage
        "api_calls_count": stats.api_calls_count,
        "last_api_call_at": stats.last_api_call_at.isoformat() if stats.last_api_call_at else None,

        # Account info
        "created_at": stats.created_at.isoformat() if stats.created_at else None,
        "user_type": "authenticated" if stats.user_id else "guest"
    }


def check_usage_limit(
    db: Session,
    user_id: int = None,
    guest_id: str = None,
    limit_type: str = "files_per_day",
    limit_value: int = 10
) -> tuple[bool, str]:
    """
    Check if user/guest has exceeded usage limits

    Args:
        db: Database session
        user_id: User ID (optional)
        guest_id: Guest ID (optional)
        limit_type: Type of limit to check
        limit_value: Maximum allowed value

    Returns:
        Tuple of (is_within_limit, message)
    """
    stats = get_or_create_usage_stats(db, user_id, guest_id)

    if limit_type == "files_per_day":
        # Check files uploaded today (simple implementation)
        # You could make this more sophisticated by tracking daily uploads
        if stats.total_files_uploaded >= limit_value:
            return False, f"Daily upload limit of {limit_value} files reached"

    elif limit_type == "storage_mb":
        total_storage_mb = stats.total_input_size / (1024 * 1024)
        if total_storage_mb >= limit_value:
            return False, f"Storage limit of {limit_value}MB reached"

    elif limit_type == "processing_minutes":
        total_minutes = stats.total_processing_time / 60
        if total_minutes >= limit_value:
            return False, f"Processing time limit of {limit_value} minutes reached"

    return True, "Within limits"
