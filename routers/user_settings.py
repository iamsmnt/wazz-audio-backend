"""User settings endpoints for profile management and usage statistics"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from wazz_shared.database import get_db
from wazz_shared.models import User, UserUsageStats
from wazz_shared.schemas import (
    UsernameUpdate,
    PasswordChange,
    UserSettingsResponse,
    UserUsageStatsResponse,
    MessageResponse,
)
from dependencies import get_current_user
from auth import get_password_hash, verify_password

router = APIRouter(prefix="/user", tags=["User Settings"])


@router.get("/settings", response_model=UserSettingsResponse)
def get_user_settings(
    current_user: User = Depends(get_current_user),
):
    """Get current user settings and profile information"""
    return UserSettingsResponse(
        user_id=current_user.id,
        username=current_user.username,
        email=current_user.email,
        is_active=current_user.is_active,
        is_verified=current_user.is_verified,
        created_at=current_user.created_at,
    )


@router.put("/settings/username", response_model=MessageResponse)
def update_username(
    username_data: UsernameUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Update username for the current user

    - Requires authentication
    - New username must be unique
    - Username must be between 3-50 characters
    """
    # Check if new username is same as current
    if username_data.new_username == current_user.username:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="New username is the same as current username",
        )

    # Check if username already exists
    existing_user = (
        db.query(User)
        .filter(User.username == username_data.new_username)
        .first()
    )
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Username already taken",
        )

    # Update username
    current_user.username = username_data.new_username
    db.commit()

    return MessageResponse(message="Username updated successfully")


@router.put("/settings/password", response_model=MessageResponse)
def change_password(
    password_data: PasswordChange,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Change password for the current user

    - Requires authentication
    - Must provide current password for verification
    - New password must be different from current password
    - New password must be between 8-72 characters
    """
    # Verify current password
    if not verify_password(password_data.current_password, current_user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Current password is incorrect",
        )

    # Check if new password is same as current
    if verify_password(password_data.new_password, current_user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="New password must be different from current password",
        )

    # Update password
    current_user.hashed_password = get_password_hash(password_data.new_password)
    db.commit()

    return MessageResponse(message="Password changed successfully")


@router.get("/usage", response_model=UserUsageStatsResponse)
def get_user_usage_statistics(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Get usage statistics for the current user

    Returns detailed metrics including:
    - File upload/processing/download counts
    - Storage usage (input/output sizes)
    - Processing time
    - Processing type breakdown
    - Activity timestamps
    - API call counts
    """
    # Fetch usage stats for current user
    stats = (
        db.query(UserUsageStats)
        .filter(UserUsageStats.user_id == current_user.id)
        .first()
    )

    # If no stats exist, return zeros
    if not stats:
        return UserUsageStatsResponse(
            total_files_uploaded=0,
            total_files_processed=0,
            total_files_failed=0,
            total_files_downloaded=0,
            total_input_size_mb=0.0,
            total_output_size_mb=0.0,
            total_processing_time_minutes=0.0,
            processing_types_count={},
            first_upload_at=None,
            last_upload_at=None,
            last_download_at=None,
            api_calls_count=0,
            last_api_call_at=None,
        )

    # Convert bytes to MB and seconds to minutes for easier reading
    return UserUsageStatsResponse(
        total_files_uploaded=stats.total_files_uploaded,
        total_files_processed=stats.total_files_processed,
        total_files_failed=stats.total_files_failed,
        total_files_downloaded=stats.total_files_downloaded,
        total_input_size_mb=round(stats.total_input_size / 1024 / 1024, 2),
        total_output_size_mb=round(stats.total_output_size / 1024 / 1024, 2),
        total_processing_time_minutes=round(stats.total_processing_time / 60, 2),
        processing_types_count=stats.processing_types_count or {},
        first_upload_at=stats.first_upload_at,
        last_upload_at=stats.last_upload_at,
        last_download_at=stats.last_download_at,
        api_calls_count=stats.api_calls_count,
        last_api_call_at=stats.last_api_call_at,
    )
