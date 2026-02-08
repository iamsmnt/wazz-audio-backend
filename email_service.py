"""
DEPRECATED: This module is superseded by the decoupled email service (wazz-audio-email/).
The backend now publishes domain events to RabbitMQ, and the email service
consumes them asynchronously. This file is retained temporarily for reference.
"""

import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta
import secrets
from wazz_shared.config import get_shared_settings

settings = get_shared_settings()


def generate_verification_token() -> str:
    """Generate a secure random verification token"""
    return secrets.token_urlsafe(32)


def create_verification_email_html(username: str, verification_url: str) -> str:
    """Create HTML email template for verification"""
    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <style>
            body {{
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
                line-height: 1.6;
                color: #334155;
                max-width: 600px;
                margin: 0 auto;
                padding: 20px;
            }}
            .container {{
                background: linear-gradient(to bottom right, #ecfeff, #dbeafe, #d1fae5);
                border-radius: 24px;
                padding: 40px;
                box-shadow: 0 10px 30px rgba(0,0,0,0.1);
            }}
            .header {{
                text-align: center;
                margin-bottom: 30px;
            }}
            .logo {{
                background: linear-gradient(to bottom right, #14b8a6, #0891b2);
                color: white;
                font-size: 32px;
                font-weight: bold;
                padding: 20px;
                border-radius: 16px;
                display: inline-block;
                margin-bottom: 20px;
            }}
            h1 {{
                background: linear-gradient(to right, #0f766e, #0e7490);
                -webkit-background-clip: text;
                -webkit-text-fill-color: transparent;
                font-size: 28px;
                margin: 0;
            }}
            .content {{
                background: rgba(255, 255, 255, 0.8);
                backdrop-filter: blur(12px);
                border: 1px solid #99f6e4;
                border-radius: 16px;
                padding: 30px;
                margin: 20px 0;
            }}
            .button {{
                display: inline-block;
                background: linear-gradient(to right, #14b8a6, #0891b2);
                color: white;
                text-decoration: none;
                padding: 14px 32px;
                border-radius: 12px;
                font-weight: bold;
                margin: 20px 0;
                box-shadow: 0 4px 12px rgba(20, 184, 166, 0.3);
            }}
            .button:hover {{
                box-shadow: 0 6px 16px rgba(20, 184, 166, 0.4);
            }}
            .footer {{
                text-align: center;
                margin-top: 30px;
                color: #64748b;
                font-size: 14px;
            }}
            .link {{
                color: #0891b2;
                word-break: break-all;
            }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <div class="logo">🎙️ Whazz Audio</div>
                <h1>Verify Your Email</h1>
            </div>

            <div class="content">
                <p>Hi {username},</p>
                <p>Thanks for signing up! Please verify your email address to start processing audio with AI.</p>
                <p style="text-align: center;">
                    <a href="{verification_url}" class="button">Verify Email Address</a>
                </p>
                <p>Or copy and paste this link into your browser:</p>
                <p class="link">{verification_url}</p>
                <p><strong>This link will expire in 24 hours.</strong></p>
            </div>

            <div class="footer">
                <p>If you didn't create an account, please ignore this email.</p>
                <p>&copy; 2026 Whazz Audio - AI-Powered Audio Processing</p>
            </div>
        </div>
    </body>
    </html>
    """


def send_verification_email(email: str, username: str, token: str) -> bool:
    """
    Send verification email to user

    Args:
        email: User's email address
        username: User's username
        token: Verification token

    Returns:
        bool: True if email sent successfully, False otherwise
    """
    try:
        # Create verification URL
        verification_url = f"{settings.frontend_url}/verify-email?token={token}"

        # Create message
        msg = MIMEMultipart('alternative')
        msg['Subject'] = 'Verify Your Whazz Audio Account'
        msg['From'] = settings.smtp_from_email
        msg['To'] = email

        # Create plain text version
        text_content = f"""
        Hi {username},

        Thanks for signing up for Whazz Audio!

        Please verify your email address by clicking the link below:
        {verification_url}

        This link will expire in 24 hours.

        If you didn't create an account, please ignore this email.

        Best regards,
        Whazz Audio Team
        """

        # Create HTML version
        html_content = create_verification_email_html(username, verification_url)

        # Attach both versions
        part1 = MIMEText(text_content, 'plain')
        part2 = MIMEText(html_content, 'html')
        msg.attach(part1)
        msg.attach(part2)

        # Send email via SMTP
        with smtplib.SMTP(settings.smtp_server, settings.smtp_port) as server:
            if settings.smtp_use_tls:
                server.starttls()
            if settings.smtp_username and settings.smtp_password:
                server.login(settings.smtp_username, settings.smtp_password)
            server.send_message(msg)

        return True

    except Exception as e:
        print(f"Failed to send verification email: {e}")
        return False


def get_verification_token_expiry() -> datetime:
    """Get expiration time for verification token (24 hours from now)"""
    return datetime.utcnow() + timedelta(hours=24)
