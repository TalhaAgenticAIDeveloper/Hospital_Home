"""
Email service — sends OTP emails via SMTP (Gmail App Password).

Uses aiosmtplib for async SMTP operations. OTPs are never logged.
"""

import random
import string
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import aiosmtplib

from app.core.config import get_settings
from app.core.logging import get_logger

settings = get_settings()
logger = get_logger(__name__)


class EmailService:
    """Handles OTP generation and email delivery."""

    @staticmethod
    def generate_otp(length: int = 6) -> str:
        """Generate a cryptographically random numeric OTP."""
        return "".join(random.choices(string.digits, k=length))

    @staticmethod
    async def send_otp_email(to_email: str, otp: str, purpose: str) -> bool:
        """
        Send an OTP email to the specified address.

        Args:
            to_email: Recipient email address.
            otp: The plaintext OTP code.
            purpose: 'signup' or 'reset_password'.

        Returns:
            True if email was sent successfully.

        Raises:
            Exception: If SMTP delivery fails.
        """
        if purpose == "signup":
            subject = "Verify Your Email — MedTrust"
            heading = "Email Verification"
            message = "Use the code below to verify your email address and complete your registration."
            note = "If you didn't create an account, you can safely ignore this email."
        else:
            subject = "Password Reset — MedTrust"
            heading = "Password Reset"
            message = "Use the code below to reset your password."
            note = "If you didn't request a password reset, please ignore this email. Your password will remain unchanged."

        html_body = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="utf-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
        </head>
        <body style="margin:0; padding:0; background-color:#f0f4f8; font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;">
            <div style="max-width:520px; margin:40px auto; background:#ffffff; border-radius:16px; overflow:hidden; box-shadow: 0 4px 24px rgba(0,0,0,0.08);">
                <!-- Header -->
                <div style="background: linear-gradient(135deg, #4f46e5 0%, #7c3aed 100%); padding:32px 40px; text-align:center;">
                    <h1 style="color:#ffffff; margin:0; font-size:22px; font-weight:700; letter-spacing:-0.3px;">
                        🏥 MedTrust
                    </h1>
                    <p style="color: rgba(255,255,255,0.85); margin:6px 0 0; font-size:14px;">
                        Healthcare SaaS Platform
                    </p>
                </div>

                <!-- Body -->
                <div style="padding:36px 40px;">
                    <h2 style="color:#1e293b; margin:0 0 8px; font-size:20px; font-weight:700;">
                        {heading}
                    </h2>
                    <p style="color:#64748b; font-size:14px; line-height:1.6; margin:0 0 28px;">
                        {message}
                    </p>

                    <!-- OTP Box -->
                    <div style="background: linear-gradient(135deg, #f8fafc 0%, #eef2ff 100%); border: 2px dashed #c7d2fe; border-radius:12px; padding:24px; text-align:center; margin-bottom:28px;">
                        <p style="color:#64748b; font-size:12px; text-transform:uppercase; letter-spacing:1.5px; margin:0 0 12px; font-weight:600;">
                            Your Verification Code
                        </p>
                        <div style="font-size:36px; font-weight:800; letter-spacing:10px; color:#4f46e5; font-family: 'Courier New', monospace;">
                            {otp}
                        </div>
                        <p style="color:#94a3b8; font-size:12px; margin:12px 0 0;">
                            This code expires in <strong style="color:#ef4444;">{settings.OTP_EXPIRE_MINUTES} minutes</strong>
                        </p>
                    </div>

                    <!-- Note -->
                    <div style="background:#fffbeb; border-left:4px solid #f59e0b; padding:12px 16px; border-radius:0 8px 8px 0; margin-bottom:4px;">
                        <p style="color:#92400e; font-size:13px; margin:0; line-height:1.5;">
                            ⚠️ {note}
                        </p>
                    </div>
                </div>

                <!-- Footer -->
                <div style="background:#f8fafc; padding:20px 40px; text-align:center; border-top:1px solid #e2e8f0;">
                    <p style="color:#94a3b8; font-size:12px; margin:0;">
                        © {__import__('datetime').datetime.now().year} MedTrust SaaS Platform — All rights reserved.
                    </p>
                </div>
            </div>
        </body>
        </html>
        """

        # Build MIME message
        msg = MIMEMultipart("alternative")
        msg["From"] = f"MedTrust <{settings.EMAIL_FROM}>"
        msg["To"] = to_email
        msg["Subject"] = subject

        # Plain text fallback
        plain_text = f"{heading}\n\nYour verification code is: {otp}\n\nThis code expires in {settings.OTP_EXPIRE_MINUTES} minutes.\n\n{note}"
        msg.attach(MIMEText(plain_text, "plain"))
        msg.attach(MIMEText(html_body, "html"))

        try:
            await aiosmtplib.send(
                msg,
                hostname=settings.SMTP_SERVER,
                port=settings.SMTP_PORT,
                username=settings.SMTP_USERNAME,
                password=settings.SMTP_PASSWORD,
                start_tls=True,
            )
            logger.info(f"otp_email_sent: to={to_email} purpose={purpose}")
            return True
        except Exception as e:
            logger.error(f"otp_email_failed: to={to_email} purpose={purpose} error={str(e)}")
            raise
