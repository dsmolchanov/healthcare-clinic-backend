"""Email service for sending invitations."""
import smtplib
import logging
import asyncio
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Optional
from app import config

logger = logging.getLogger(__name__)

class EmailService:
    """Service for sending emails via SMTP."""

    def __init__(self):
        self.host = config.SMTP_HOST
        self.port = config.SMTP_PORT
        self.username = config.SMTP_USERNAME
        self.password = config.SMTP_PASSWORD
        self.from_email = config.SMTP_FROM_EMAIL
        self.from_name = config.SMTP_FROM_NAME
        self.use_tls = config.SMTP_USE_TLS

    def send_email_sync(
        self,
        to_email: str,
        subject: str,
        html_content: str,
        text_content: Optional[str] = None
    ) -> bool:
        """Send email via SMTP (synchronous, use via asyncio.to_thread)."""
        try:
            msg = MIMEMultipart('alternative')
            msg['Subject'] = subject
            msg['From'] = f"{self.from_name} <{self.from_email}>"
            msg['To'] = to_email

            # Add text and HTML parts
            if text_content:
                msg.attach(MIMEText(text_content, 'plain'))
            msg.attach(MIMEText(html_content, 'html'))

            # Send via SMTP
            with smtplib.SMTP(self.host, self.port) as server:
                if self.use_tls:
                    server.starttls()
                if self.username and self.password:
                    server.login(self.username, self.password)
                server.send_message(msg)

            logger.info(f"Email sent to {to_email}")
            return True

        except Exception as e:
            logger.error(f"Failed to send email to {to_email}: {e}")
            return False

    async def send_email(
        self,
        to_email: str,
        subject: str,
        html_content: str,
        text_content: Optional[str] = None
    ) -> bool:
        """Send email via SMTP (async wrapper to avoid blocking event loop)."""
        return await asyncio.to_thread(
            self.send_email_sync,
            to_email,
            subject,
            html_content,
            text_content
        )

    async def send_invitation(
        self,
        to_email: str,
        inviter_name: str,
        clinic_name: str,
        role: str,
        invitation_url: str
    ) -> bool:
        """Send invitation email (async)."""
        subject = f"{inviter_name} invited you to join {clinic_name}"

        html_content = f"""
        <html>
        <body style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
            <h2>You've been invited to join {clinic_name}</h2>
            <p>{inviter_name} has invited you to join their clinic on PlainTalk as a <strong>{role}</strong>.</p>
            <p>Click the button below to accept the invitation and create your account:</p>
            <p style="text-align: center; margin: 30px 0;">
                <a href="{invitation_url}"
                   style="background-color: #4F46E5; color: white; padding: 12px 24px;
                          text-decoration: none; border-radius: 6px; display: inline-block;">
                    Accept Invitation
                </a>
            </p>
            <p style="color: #666; font-size: 14px;">
                This invitation link will expire in 7 days.
            </p>
            <p style="color: #666; font-size: 14px;">
                If you didn't expect this invitation, you can safely ignore this email.
            </p>
        </body>
        </html>
        """

        text_content = f"""
        You've been invited to join {clinic_name}

        {inviter_name} has invited you to join their clinic on PlainTalk as a {role}.

        Click this link to accept: {invitation_url}

        This invitation expires in 7 days.
        """

        return await self.send_email(to_email, subject, html_content, text_content)

    async def send_sales_invitation(
        self,
        to_email: str,
        inviter_name: str,
        org_name: str,
        role: str,
        invitation_url: str
    ) -> bool:
        """Send sales team invitation email (async)."""
        role_display = {
            'admin': 'Administrator',
            'manager': 'Manager',
            'rep': 'Sales Representative'
        }.get(role, role.title())

        subject = f"{inviter_name} invited you to join {org_name}"

        html_content = f"""
        <html>
        <body style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
            <h2>You've been invited to join {org_name}</h2>
            <p>{inviter_name} has invited you to join the sales team as a <strong>{role_display}</strong>.</p>
            <p>Click the button below to accept the invitation and create your account:</p>
            <p style="text-align: center; margin: 30px 0;">
                <a href="{invitation_url}"
                   style="background-color: #10B981; color: white; padding: 12px 24px;
                          text-decoration: none; border-radius: 6px; display: inline-block;">
                    Accept Invitation
                </a>
            </p>
            <p style="color: #666; font-size: 14px;">
                This invitation link will expire in 7 days.
            </p>
            <p style="color: #666; font-size: 14px;">
                If you didn't expect this invitation, you can safely ignore this email.
            </p>
        </body>
        </html>
        """

        text_content = f"""
        You've been invited to join {org_name}

        {inviter_name} has invited you to join the sales team as a {role_display}.

        Click this link to accept: {invitation_url}

        This invitation expires in 7 days.
        """

        return await self.send_email(to_email, subject, html_content, text_content)

_email_service: Optional[EmailService] = None

def get_email_service() -> EmailService:
    """Get email service singleton."""
    global _email_service
    if _email_service is None:
        _email_service = EmailService()
    return _email_service
