"""Authentication routes for signup, login, logout, email verification, password reset"""

import logging
import secrets
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy.orm import Session

from wazz_shared.config import get_shared_settings
from wazz_shared.database import get_db
from wazz_shared.models import User, TokenBlacklist
from wazz_shared.schemas import (
    UserCreate,
    UserLogin,
    UserResponse,
    Token,
    RefreshTokenRequest,
    MessageResponse,
    ForgotPasswordRequest,
    ResetPasswordRequest,
    VerifyEmailRequest,
    ResendVerificationRequest,
)
from wazz_shared.events import (
    UserRegisteredEvent,
    UserVerifiedEvent,
    UserPasswordResetRequestedEvent,
)
from auth import (
    get_password_hash,
    verify_password,
    create_access_token,
    create_refresh_token,
    verify_token,
    get_token_expiration,
)
from dependencies import get_current_user, security

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["Authentication"])
settings = get_shared_settings()

# Event publisher — set from main.py at startup
_event_publisher = None


def set_event_publisher(publisher):
    """Wire the EventPublisher instance into this module."""
    global _event_publisher
    _event_publisher = publisher


def _publish_event(event):
    """Fire-and-forget event publishing. Never raises."""
    if _event_publisher:
        try:
            _event_publisher.publish(event)
        except Exception as e:
            logger.error(f"Failed to publish {event.event_type}: {e}")


@router.post("/signup", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
def signup(user_data: UserCreate, db: Session = Depends(get_db)):
    """Register a new user"""

    # Check if email already exists
    existing_email = db.query(User).filter(User.email == user_data.email).first()
    if existing_email:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Email already registered"
        )

    # Check if username already exists
    existing_username = db.query(User).filter(User.username == user_data.username).first()
    if existing_username:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Username already taken"
        )

    # Create new user with verification token
    hashed_password = get_password_hash(user_data.password)
    verification_token = secrets.token_urlsafe(32)
    verification_expires = datetime.utcnow() + timedelta(hours=24)

    new_user = User(
        email=user_data.email,
        username=user_data.username,
        hashed_password=hashed_password,
        verification_token=verification_token,
        verification_token_expires=verification_expires,
    )

    db.add(new_user)
    db.commit()
    db.refresh(new_user)

    # Publish event — signup succeeds even if publish fails
    _publish_event(UserRegisteredEvent(
        timestamp=datetime.utcnow(),
        user_id=new_user.id,
        email=new_user.email,
        username=new_user.username,
        verification_token=verification_token,
        verification_token_expires=verification_expires,
        frontend_url=settings.frontend_url,
    ))

    return new_user


@router.post("/login", response_model=Token)
def login(login_data: UserLogin, db: Session = Depends(get_db)):
    """Login user and return access and refresh tokens"""

    # Find user by email or username
    user = (
        db.query(User)
        .filter(
            (User.email == login_data.username_or_email)
            | (User.username == login_data.username_or_email)
        )
        .first()
    )

    # Verify user exists and password is correct
    if not user or not verify_password(login_data.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username/email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Check if user is active
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="User account is inactive"
        )

    # Create access and refresh tokens
    # Note: 'sub' must be a string per JWT spec
    access_token = create_access_token(data={"sub": str(user.id), "username": user.username})
    refresh_token = create_refresh_token(data={"sub": str(user.id), "username": user.username})

    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
    }


@router.post("/logout", response_model=MessageResponse)
def logout(
    credentials: HTTPAuthorizationCredentials = Depends(security), db: Session = Depends(get_db)
):
    """Logout user by blacklisting the token"""

    token = credentials.credentials

    # Check if token is already blacklisted
    existing_blacklist = db.query(TokenBlacklist).filter(TokenBlacklist.token == token).first()
    if existing_blacklist:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Token already invalidated"
        )

    # Get token expiration
    expiration = get_token_expiration(token)
    if not expiration:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token"
        )

    # Add token to blacklist
    blacklisted_token = TokenBlacklist(token=token, expires_at=expiration)
    db.add(blacklisted_token)
    db.commit()

    return {"message": "Successfully logged out"}


@router.post("/refresh", response_model=Token)
def refresh_access_token(refresh_data: RefreshTokenRequest, db: Session = Depends(get_db)):
    """Refresh access token using refresh token"""

    # Verify refresh token
    payload = verify_token(refresh_data.refresh_token)
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid refresh token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Check token type
    if payload.get("type") != "refresh":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token type",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Check if token is blacklisted
    blacklisted = (
        db.query(TokenBlacklist)
        .filter(TokenBlacklist.token == refresh_data.refresh_token)
        .first()
    )
    if blacklisted:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has been revoked",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user_id_str = payload.get("sub")
    username = payload.get("username")

    if not user_id_str:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token payload"
        )

    # Convert string user_id to integer for database query
    try:
        user_id = int(user_id_str)
    except (ValueError, TypeError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token payload"
        )

    # Verify user still exists and is active
    user = db.query(User).filter(User.id == user_id).first()
    if not user or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found or inactive"
        )

    # Create new access and refresh tokens (sub must be string)
    access_token = create_access_token(data={"sub": str(user.id), "username": username})
    new_refresh_token = create_refresh_token(data={"sub": str(user.id), "username": username})

    return {
        "access_token": access_token,
        "refresh_token": new_refresh_token,
        "token_type": "bearer",
    }


@router.post("/verify-email", response_model=MessageResponse)
def verify_email(data: VerifyEmailRequest, db: Session = Depends(get_db)):
    """Verify user email with token"""

    user = db.query(User).filter(User.verification_token == data.token).first()

    if not user:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid verification token")

    if user.verification_token_expires and user.verification_token_expires.replace(tzinfo=None) < datetime.utcnow():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Verification token has expired")

    user.is_verified = True
    user.verification_token = None
    user.verification_token_expires = None
    db.commit()

    # Publish event — triggers welcome email
    _publish_event(UserVerifiedEvent(
        timestamp=datetime.utcnow(),
        user_id=user.id,
        email=user.email,
        username=user.username,
        frontend_url=settings.frontend_url,
    ))

    return {"message": "Email verified successfully"}


@router.post("/forgot-password", response_model=MessageResponse)
def forgot_password(data: ForgotPasswordRequest, db: Session = Depends(get_db)):
    """Request a password reset email. Always returns success to prevent email enumeration."""

    user = db.query(User).filter(User.email == data.email).first()

    if user and user.is_active:
        reset_token = secrets.token_urlsafe(32)
        reset_expires = datetime.utcnow() + timedelta(
            hours=settings.password_reset_token_expire_hours
        )
        user.password_reset_token = reset_token
        user.password_reset_token_expires = reset_expires
        db.commit()

        _publish_event(UserPasswordResetRequestedEvent(
            timestamp=datetime.utcnow(),
            user_id=user.id,
            email=user.email,
            username=user.username,
            reset_token=reset_token,
            reset_token_expires=reset_expires,
            frontend_url=settings.frontend_url,
        ))

    return {"message": "If an account with that email exists, a password reset link has been sent"}


@router.post("/reset-password", response_model=MessageResponse)
def reset_password(data: ResetPasswordRequest, db: Session = Depends(get_db)):
    """Reset password using the reset token"""

    user = db.query(User).filter(User.password_reset_token == data.token).first()

    if not user:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid reset token")

    if user.password_reset_token_expires and user.password_reset_token_expires.replace(tzinfo=None) < datetime.utcnow():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Reset token has expired")

    user.hashed_password = get_password_hash(data.new_password)
    user.password_reset_token = None
    user.password_reset_token_expires = None
    db.commit()

    return {"message": "Password has been reset successfully"}


@router.post("/resend-verification", response_model=MessageResponse)
def resend_verification(data: ResendVerificationRequest, db: Session = Depends(get_db)):
    """Resend the verification email. Always returns success to prevent enumeration."""

    user = db.query(User).filter(User.email == data.email).first()

    if user and not user.is_verified and user.is_active:
        verification_token = secrets.token_urlsafe(32)
        verification_expires = datetime.utcnow() + timedelta(hours=24)
        user.verification_token = verification_token
        user.verification_token_expires = verification_expires
        db.commit()

        _publish_event(UserRegisteredEvent(
            timestamp=datetime.utcnow(),
            user_id=user.id,
            email=user.email,
            username=user.username,
            verification_token=verification_token,
            verification_token_expires=verification_expires,
            frontend_url=settings.frontend_url,
        ))

    return {"message": "If your account requires verification, a new email has been sent"}


@router.get("/me", response_model=UserResponse)
def get_current_user_info(current_user: User = Depends(get_current_user)):
    """Get current user information"""
    return current_user
