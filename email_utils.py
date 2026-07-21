from email.message import EmailMessage

import aiosmtplib
from fastapi.templating import Jinja2Templates

from config import settings

templates = Jinja2Templates(directory="templates")


async def send_email(
    to_email: str,
    subject: str,
    plain_text: str,
    html_content: str | None = None,
) -> None:
    """Send an email via SMTP using aiosmtplib (async, non-blocking)."""
    message = EmailMessage()
    message["From"] = settings.mail_from
    message["To"] = to_email
    message["Subject"] = subject

    message.set_content(plain_text)

    if html_content:
        message.add_alternative(html_content, subtype="html")

    await aiosmtplib.send(
        message,
        hostname=settings.mail_server,
        port=settings.mail_port,
        username=settings.mail_username,
        password=settings.mail_password.get_secret_value(),
        start_tls=True,
    )


async def send_password_reset_email(to_email: str, reset_url: str) -> None:
    """Render the password-reset email template and send it."""
    subject = "Password Reset Request"

    plain_text = (
        f"You requested a password reset.\n\n"
        f"Click the link below to reset your password:\n{reset_url}\n\n"
        f"This link will expire in {settings.reset_token_expire_minutes} minutes.\n\n"
        f"If you did not request a password reset, please ignore this email."
    )

    # Render the HTML email template
    html_template = templates.get_template("email/password_reset.html")
    html_content = html_template.render(
        reset_url=reset_url,
        expire_minutes=settings.reset_token_expire_minutes,
    )

    await send_email(to_email, subject, plain_text, html_content)