"""Authentication utilities for JWT tokens and password hashing"""

from datetime import datetime, timedelta
from typing import Optional
from jose import JWTError, jwt
from passlib.context import CryptContext
from wazz_shared.config import get_shared_settings

settings = get_shared_settings()

# Password hashing context
# Configure bcrypt to automatically truncate passwords to 72 bytes
pwd_context = CryptContext(
    schemes=["bcrypt"],
    deprecated="auto",
    bcrypt__truncate_error=False  # Don't raise error, just truncate
)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a password against its hash"""
    # Truncate password to 72 bytes for bcrypt compatibility (bcrypt 4.x requirement)
    if len(plain_password) > 72:
        plain_password = plain_password[:72]

    # Additional check for byte length
    password_bytes = plain_password.encode('utf-8')
    while len(password_bytes) > 72:
        plain_password = plain_password[:-1]
        password_bytes = plain_password.encode('utf-8')

    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password: str) -> str:
    """Hash a password using bcrypt (max 72 bytes)"""
    # Bcrypt can only handle passwords up to 72 bytes
    # Truncate to 72 characters as a safety measure (usually characters <= bytes)
    # For proper handling, validation should happen in schemas
    if len(password) > 72:
        password = password[:72]

    # Additional check for byte length
    password_bytes = password.encode('utf-8')
    while len(password_bytes) > 72:
        password = password[:-1]
        password_bytes = password.encode('utf-8')

    return pwd_context.hash(password)


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """Create a JWT access token"""
    to_encode = data.copy()

    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=settings.access_token_expire_minutes)

    to_encode.update({"exp": expire, "type": "access"})
    encoded_jwt = jwt.encode(to_encode, settings.secret_key, algorithm=settings.algorithm)
    return encoded_jwt


def create_refresh_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """Create a JWT refresh token"""
    to_encode = data.copy()

    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(days=settings.refresh_token_expire_days)

    to_encode.update({"exp": expire, "type": "refresh"})
    encoded_jwt = jwt.encode(to_encode, settings.secret_key, algorithm=settings.algorithm)
    return encoded_jwt


def create_guest_token(guest_id: str, expires_delta: Optional[timedelta] = None) -> tuple[str, datetime]:
    """Create a JWT token for guest users

    Returns:
        tuple: (token, expiration_datetime)
    """
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        # Guest tokens expire in 7 days by default
        expire = datetime.utcnow() + timedelta(days=7)

    to_encode = {
        "sub": guest_id,
        "type": "guest",
        "exp": expire
    }

    encoded_jwt = jwt.encode(to_encode, settings.secret_key, algorithm=settings.algorithm)
    return encoded_jwt, expire


def verify_token(token: str) -> Optional[dict]:
    """Verify and decode a JWT token"""
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[settings.algorithm])
        return payload
    except JWTError:
        return None


def get_token_expiration(token: str) -> Optional[datetime]:
    """Get the expiration datetime from a token"""
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[settings.algorithm])
        exp_timestamp = payload.get("exp")
        if exp_timestamp:
            return datetime.fromtimestamp(exp_timestamp)
        return None
    except JWTError:
        return None
