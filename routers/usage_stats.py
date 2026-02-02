"""Usage statistics endpoints"""

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from typing import Optional
from wazz_shared.database import get_db
from dependencies import get_optional_current_user, get_current_admin_user
from wazz_shared.models import User, UserUsageStats
from wazz_shared.usage_tracking import get_usage_stats, check_usage_limit
from sqlalchemy import func

router = APIRouter(prefix="/usage", tags=["usage"])


@router.get("/me")
async def get_my_usage_stats(
    request: Request,
    current_user: Optional[User] = Depends(get_optional_current_user),
    db: Session = Depends(get_db)
):
    """Get usage statistics for current user or guest"""

    user_id = current_user.id if current_user else None
    guest_id = request.headers.get("X-Guest-ID") if not current_user else None

    if not user_id and not guest_id:
        raise HTTPException(status_code=401, detail="Not authenticated")

    stats = get_usage_stats(db, user_id=user_id, guest_id=guest_id)
    return stats


@router.get("/limits")
async def check_my_usage_limits(
    request: Request,
    current_user: Optional[User] = Depends(get_optional_current_user),
    db: Session = Depends(get_db)
):
    """Check if user/guest is within usage limits"""

    user_id = current_user.id if current_user else None
    guest_id = request.headers.get("X-Guest-ID") if not current_user else None

    if not user_id and not guest_id:
        raise HTTPException(status_code=401, detail="Not authenticated")

    # Define limits (different for users vs guests)
    if user_id:
        limits = {
            "files_per_day": 100,
            "storage_mb": 1000,
            "processing_minutes": 60
        }
    else:
        limits = {
            "files_per_day": 5,
            "storage_mb": 50,
            "processing_minutes": 10
        }

    results = {}
    for limit_type, limit_value in limits.items():
        within_limit, message = check_usage_limit(
            db,
            user_id=user_id,
            guest_id=guest_id,
            limit_type=limit_type,
            limit_value=limit_value
        )
        results[limit_type] = {
            "within_limit": within_limit,
            "limit": limit_value,
            "message": message
        }

    return results


@router.get("/admin/stats")
async def get_platform_stats(
    current_admin: User = Depends(get_current_admin_user),
    db: Session = Depends(get_db)
):
    """Admin endpoint: Get platform-wide statistics"""

    # Total users
    total_users = db.query(User).count()

    # Total guests
    total_guests = db.query(UserUsageStats).filter(
        UserUsageStats.guest_id.isnot(None)
    ).count()

    # Aggregate statistics
    all_stats = db.query(UserUsageStats).all()
    total_files_uploaded = sum(s.total_files_uploaded for s in all_stats)
    total_files_processed = sum(s.total_files_processed for s in all_stats)
    total_files_failed = sum(s.total_files_failed for s in all_stats)
    total_storage_mb = sum(s.total_input_size for s in all_stats) / (1024 * 1024)
    total_processing_minutes = sum(s.total_processing_time for s in all_stats) / 60

    # Processing types breakdown
    processing_breakdown = {}
    for stats in all_stats:
        if stats.processing_types_count:
            for ptype, count in stats.processing_types_count.items():
                processing_breakdown[ptype] = processing_breakdown.get(ptype, 0) + count

    # Success rate
    total_attempts = total_files_processed + total_files_failed
    success_rate = (total_files_processed / total_attempts * 100) if total_attempts > 0 else 0

    return {
        "users": {
            "total_registered": total_users,
            "total_guests": total_guests
        },
        "files": {
            "total_uploaded": total_files_uploaded,
            "total_processed": total_files_processed,
            "total_failed": total_files_failed,
            "success_rate_percent": round(success_rate, 2)
        },
        "storage": {
            "total_mb": round(total_storage_mb, 2)
        },
        "processing": {
            "total_minutes": round(total_processing_minutes, 2),
            "types_breakdown": processing_breakdown
        }
    }


@router.get("/admin/user/{user_id}")
async def get_user_stats_admin(
    user_id: int,
    current_admin: User = Depends(get_current_admin_user),
    db: Session = Depends(get_db)
):
    """Admin endpoint: Get usage stats for a specific user"""

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    stats = get_usage_stats(db, user_id=user_id)

    return {
        "user": {
            "id": user.id,
            "username": user.username,
            "email": user.email,
            "is_verified": user.is_verified,
            "created_at": user.created_at.isoformat() if user.created_at else None
        },
        "stats": stats
    }


@router.get("/admin/guest/{guest_id}")
async def get_guest_stats_admin(
    guest_id: str,
    current_admin: User = Depends(get_current_admin_user),
    db: Session = Depends(get_db)
):
    """Admin endpoint: Get usage stats for a specific guest"""

    stats = get_usage_stats(db, guest_id=guest_id)

    return {
        "guest_id": guest_id,
        "stats": stats
    }


@router.get("/admin/top-users")
async def get_top_users(
    limit: int = 10,
    sort_by: str = "files_processed",
    current_admin: User = Depends(get_current_admin_user),
    db: Session = Depends(get_db)
):
    """Admin endpoint: Get top users by various metrics"""

    # Map sort_by to column
    sort_columns = {
        "files_processed": UserUsageStats.total_files_processed,
        "files_uploaded": UserUsageStats.total_files_uploaded,
        "storage": UserUsageStats.total_input_size,
        "processing_time": UserUsageStats.total_processing_time,
        "api_calls": UserUsageStats.api_calls_count
    }

    sort_column = sort_columns.get(sort_by, UserUsageStats.total_files_processed)

    # Query top users (only authenticated users)
    top_stats = db.query(UserUsageStats, User).join(
        User, UserUsageStats.user_id == User.id
    ).filter(
        UserUsageStats.user_id.isnot(None)
    ).order_by(
        sort_column.desc()
    ).limit(limit).all()

    results = []
    for stats, user in top_stats:
        results.append({
            "user": {
                "id": user.id,
                "username": user.username,
                "email": user.email
            },
            "stats": get_usage_stats(db, user_id=user.id)
        })

    return {
        "sort_by": sort_by,
        "limit": limit,
        "results": results
    }
