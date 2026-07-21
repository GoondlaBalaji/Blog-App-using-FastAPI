# 9. Password Reset & Background Tasks

> **What changed:** This note covers everything added in Part 14 on top of the pagination code.
> The core feature is a complete **password reset flow** (forgot password via email, reset via token link)
> and a **change password** feature for logged-in users. FastAPI's `BackgroundTasks` is used to send
> emails without making the user wait.

---

## Table of Contents

1. [The Full Password Reset Flow — Big Picture](#1-the-full-password-reset-flow--big-picture)
2. [New Concepts: Background Tasks](#2-new-concepts-background-tasks)
3. [New File: `email_utils.py`](#3-new-file-email_utilspy)
4. [Updated: `auth.py` — Reset Token Helpers](#4-updated-authpy--reset-token-helpers)
5. [Updated: `models.py` — `PasswordResetToken` Table](#5-updated-modelspy--passwordresettoken-table)
6. [Updated: `schemas.py` — New Request Schemas](#6-updated-schemaspy--new-request-schemas)
7. [Updated: `config.py` — SMTP Mail Settings](#7-updated-configpy--smtp-mail-settings)
8. [Updated: `routers/users.py` — Three New Endpoints](#8-updated-routersuserspy--three-new-endpoints)
9. [Updated: `main.py` — Two New Page Routes](#9-updated-mainpy--two-new-page-routes)
10. [New Templates](#10-new-templates)
11. [Updated: `account.html` — Working Change Password Form](#11-updated-accounthtml--working-change-password-form)
12. [Key Concepts Explained](#12-key-concepts-explained)

---

## 1. The Full Password Reset Flow — Big Picture

There are **two separate flows** in Part 14:

### Flow A — Forgot Password (unauthenticated user)
```
User visits /forgot-password
     │
     └─ Types email → submits form
          │
          └─ POST /api/users/forgot-password
               │
               ├─ Look up user by email in DB
               ├─ Generate random raw token (secrets.token_urlsafe)
               ├─ Hash the token with SHA-256 → store ONLY the hash in DB
               ├─ Schedule email as BackgroundTask (returns 202 immediately)
               └─ Email arrives: "Click here to reset: /reset-password?token=<raw_token>"
                    │
                    └─ User clicks link → /reset-password?token=<raw_token>
                         │
                         └─ Types new password → submits form
                              │
                              └─ POST /api/users/reset-password
                                   │
                                   ├─ Hash the token from URL → look up hash in DB
                                   ├─ Verify token not used + not expired
                                   ├─ Update user.password_hash
                                   └─ Mark token as used → redirect to /login
```

### Flow B — Change Password (logged-in user)
```
User visits /account
     │
     └─ Fills Change Password form (current + new + confirm)
          │
          └─ POST /api/users/{user_id}/change-password
               │
               ├─ Verify current_user.id == user_id (authorization)
               ├─ verify_password(current_password, stored_hash)
               └─ Update password_hash → return 200 OK
```

---

## 2. New Concepts: Background Tasks

### The Problem
Sending an email takes ~200-500ms (network roundtrip to SMTP server). If we sent it synchronously, the user's browser would sit waiting for that whole time before getting a response.

### The Solution — `BackgroundTasks`
FastAPI's `BackgroundTasks` lets you schedule work to happen **after** the HTTP response is sent. The user gets their response immediately.

```python
from fastapi import BackgroundTasks

@router.post("/forgot-password", status_code=status.HTTP_202_ACCEPTED)
async def forgot_password(
    request: Request,
    body: ForgotPasswordRequest,
    background_tasks: BackgroundTasks,   # ← injected by FastAPI
    db: Annotated[AsyncSession, Depends(get_db)],
):
    # ... create token in DB ...

    # Schedule email — does NOT block. Returns 202 immediately.
    background_tasks.add_task(send_password_reset_email, user.email, reset_url)

    return {"message": "..."}
```

### How `add_task()` Works
```python
background_tasks.add_task(function, arg1, arg2, keyword=value)
# Equivalent to calling: function(arg1, arg2, keyword=value) — but AFTER the response
```

- The function is called **after** FastAPI sends the HTTP response back to the client
- The client sees `202 Accepted` immediately — they don't wait for the email
- If the email fails, the user has already gotten their response — you'd handle errors with logging

### Why 202 (Accepted) not 200 (OK)?
- `200 OK` means "I did the thing"
- `202 Accepted` means "I received your request and will process it" — semantically correct for async work

---

## 3. New File: `email_utils.py`

This entire file is new. It wraps `aiosmtplib` (async SMTP library) for non-blocking email sending.

```python
from email.message import EmailMessage

import aiosmtplib
from fastapi.templating import Jinja2Templates

from config import settings

templates = Jinja2Templates(directory="templates")
```

### `send_email()` — Core Email Sender

```python
async def send_email(
    to_email: str,
    subject: str,
    plain_text: str,
    html_content: str | None = None,
) -> None:
    message = EmailMessage()
    message["From"] = settings.mail_from
    message["To"] = to_email
    message["Subject"] = subject

    message.set_content(plain_text)          # plain text fallback

    if html_content:
        message.add_alternative(html_content, subtype="html")   # HTML version

    await aiosmtplib.send(
        message,
        hostname=settings.mail_server,
        port=settings.mail_port,
        username=settings.mail_username,
        password=settings.mail_password.get_secret_value(),
        start_tls=True,
    )
```

**Key points:**
- `EmailMessage` is from Python's standard library `email.message` — no extra install needed
- `set_content(plain_text)` sets the plain-text body (always send plain text as a fallback for email clients that don't render HTML)
- `add_alternative(html_content, subtype="html")` adds the HTML version — email clients that support HTML will show this instead
- `start_tls=True` upgrades the connection to TLS after connecting (used by Gmail, Mailtrap, etc.)
- `aiosmtplib.send()` is `async` — it does not block the event loop

### `send_password_reset_email()` — High-Level Helper

```python
async def send_password_reset_email(to_email: str, reset_url: str) -> None:
    subject = "Password Reset Request"

    plain_text = (
        f"You requested a password reset.\n\n"
        f"Click the link below to reset your password:\n{reset_url}\n\n"
        f"This link will expire in {settings.reset_token_expire_minutes} minutes.\n\n"
        f"If you did not request a password reset, please ignore this email."
    )

    html_template = templates.get_template("email/password_reset.html")
    html_content = html_template.render(
        reset_url=reset_url,
        expire_minutes=settings.reset_token_expire_minutes,
    )

    await send_email(to_email, subject, plain_text, html_content)
```

- `templates.get_template("email/password_reset.html")` — accesses the Jinja2 template directly
- `.render(...)` renders the template to a string (not an HTTP response) — gives us the HTML string to embed in the email
- The template lives at `templates/email/password_reset.html`

> **Why separate functions?** `send_email()` is a reusable generic utility. `send_password_reset_email()` is specific to this feature. If you add more email types (e.g., welcome email), you just add another function that calls `send_email()`.

---

## 4. Updated: `auth.py` — Reset Token Helpers

Two new functions were added (plus `import hashlib` and `import secrets`):

```python
import hashlib
import secrets

def generate_reset_token() -> str:
    """Generate a secure random URL-safe token for password reset."""
    return secrets.token_urlsafe(32)

def hash_reset_token(token: str) -> str:
    """Hash a reset token with SHA-256 before storing in the DB."""
    return hashlib.sha256(token.encode()).hexdigest()
```

### Why `secrets.token_urlsafe(32)`?
- `secrets` is the Python standard library module for **cryptographically secure** random values
- `token_urlsafe(32)` generates 32 bytes of random data, Base64-encoded → ~43 character string safe for use in URLs
- **Never use `random` module for security tokens** — `random` is not cryptographically secure (predictable)

### Why Hash the Token Before Storing?
This is the critical security design of the reset flow:

```
What gets emailed to user:  raw_token  (e.g. "abc123xyz...")
What gets stored in DB:     SHA256(raw_token)  (e.g. "9f86d081...")
```

**Threat model:** If your database is breached, the attacker sees only hashes. They cannot reverse a SHA-256 hash to get the raw token, so they cannot use stolen hashes to reset passwords.

```python
# In the "forgot password" endpoint:
raw_token = generate_reset_token()      # sent to user via email
token_hash = hash_reset_token(raw_token)  # stored in DB

# In the "reset password" endpoint:
token_hash = hash_reset_token(body.token)  # hash what the user sent
# then look up token_hash in DB — if found, it's valid
```

This is the **same pattern** used to store API keys securely (GitHub, Stripe, etc. all do this).

### Why SHA-256 (Not bcrypt)?
- Reset tokens are already long, random strings with enormous entropy — they don't need the slow key-stretching that bcrypt provides (bcrypt is for passwords which users choose and can be weak)
- SHA-256 is fast, deterministic, and sufficient here — just needs to be one-way

---

## 5. Updated: `models.py` — `PasswordResetToken` Table

A brand new SQLAlchemy model / database table:

```python
from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
# ^^^ added Boolean import

class PasswordResetToken(Base):
    __tablename__ = "password_reset_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"),
        nullable=False,
        index=True,
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    user: Mapped[User] = relationship(back_populates="reset_tokens")
```

| Column | Type | Purpose |
|--------|------|---------|
| `id` | Integer PK | Standard primary key |
| `user_id` | FK → users.id | Which user this token belongs to |
| `token_hash` | String(64), unique | SHA-256 hash of the raw token (64 hex chars) |
| `expires_at` | DateTime(tz=True) | When the token becomes invalid |
| `used` | Boolean | Prevents reuse of already-used tokens |

### Also added to the `User` model:
```python
reset_tokens: Mapped[list[PasswordResetToken]] = relationship(
    back_populates="user",
    cascade="all, delete-orphan",
)
```
- `cascade="all, delete-orphan"` — when a user is deleted, all their reset tokens are automatically deleted too (no orphaned rows in the DB)

> **Why `String(64)` for `token_hash`?** SHA-256 always produces a 256-bit hash = 64 hexadecimal characters. Always the same size, so `String(64)` is exact.

> **Why `timezone=True` on `expires_at`?** Timezone-aware datetimes prevent bugs when comparing. `datetime.now(UTC)` (timezone-aware) compared to a naive datetime would crash.

---

## 6. Updated: `schemas.py` — New Request Schemas

Three new schemas added at the bottom:

```python
class ForgotPasswordRequest(BaseModel):
    email: EmailStr

class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str = Field(min_length=8)

class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8)
```

**Why no `confirm_password` field in `ResetPasswordRequest`?**
Password confirmation matching (`new_password == confirm_password`) is a client-side UI concern, not a server-side validation concern. The JS in `reset_password.html` checks this before submitting:
```javascript
if (newPassword !== confirmPassword) {
    showModal('errorModal');
    return;  // don't submit
}
```
The server only needs `new_password` — it trusts the client already validated the confirmation.

---

## 7. Updated: `config.py` — SMTP Mail Settings

```python
class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",    # ← NEW: ignore unknown env vars (like MAIL_USE_TLS)
    )

    # ... existing settings ...

    reset_token_expire_minutes: int = 30   # NEW: token lifetime

    # NEW: Email / SMTP settings
    mail_server: str = "smtp.gmail.com"
    mail_port: int = 587
    mail_username: str = ""
    mail_password: SecretStr = SecretStr("")
    mail_from: str = ""
```

Your `.env` already has these set for Mailtrap (a testing SMTP sandbox):
```
MAIL_SERVER=sandbox.smtp.mailtrap.io
MAIL_PORT=2525
MAIL_USERNAME=3ddac994d241ea
MAIL_PASSWORD=7b5c553dea733d
MAIL_FROM=noreply@fastapiblog.com
```

> **Why `extra="ignore"`?** Your `.env` has `MAIL_USE_TLS=true` and `FRONTEND_URL=http://localhost:8000` which aren't defined in `Settings`. Without `extra="ignore"`, pydantic-settings raises a `ValidationError`. With it, unknown keys are silently ignored.

> **Why `SecretStr` for `mail_password`?** Same reason as `secret_key` — prevents the password from being accidentally printed in logs or error messages.

---

## 8. Updated: `routers/users.py` — Three New Endpoints

### New imports added:
```python
from datetime import UTC, datetime, timedelta      # UTC and datetime added
from fastapi import ..., BackgroundTasks, Request  # BackgroundTasks, Request added
from sqlalchemy import delete as sql_delete        # for deleting old tokens
from auth import ..., generate_reset_token, hash_reset_token  # two new auth helpers
from email_utils import send_password_reset_email   # new email module
from schemas import ..., ChangePasswordRequest, ForgotPasswordRequest, ResetPasswordRequest
```

---

### Endpoint 1: `POST /api/users/forgot-password`

```python
@router.post("/forgot-password", status_code=status.HTTP_202_ACCEPTED)
async def forgot_password(
    request: Request,
    body: ForgotPasswordRequest,
    background_tasks: BackgroundTasks,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    result = await db.execute(
        select(models.User).where(
            func.lower(models.User.email) == body.email.lower(),
        ),
    )
    user = result.scalars().first()

    if user:
        # Invalidate any existing unused tokens for this user
        await db.execute(
            sql_delete(models.PasswordResetToken).where(
                models.PasswordResetToken.user_id == user.id,
                models.PasswordResetToken.used.is_(False),
            ),
        )

        # Generate a fresh token
        raw_token = generate_reset_token()
        token_hash = hash_reset_token(raw_token)
        expires_at = datetime.now(UTC) + timedelta(minutes=settings.reset_token_expire_minutes)

        reset_token = models.PasswordResetToken(
            user_id=user.id,
            token_hash=token_hash,
            expires_at=expires_at,
        )
        db.add(reset_token)
        await db.commit()

        base_url = str(request.base_url).rstrip("/")
        reset_url = f"{base_url}/reset-password?token={raw_token}"
        background_tasks.add_task(send_password_reset_email, user.email, reset_url)

    # Always return 202 to prevent user-enumeration
    return {"message": "If an account with that email exists, a reset link has been sent."}
```

**Key decisions explained:**

| Decision | Reason |
|----------|--------|
| Always returns 202 (even if email not found) | **User enumeration prevention** — if we returned 404 for unknown emails, attackers could discover which emails are registered |
| Delete existing unused tokens before creating new one | Ensures only one valid token per user at a time. Prevents accumulation of tokens |
| `request.base_url` | Dynamically builds the reset URL. Works in development (`http://localhost:8000`) and production (`https://yourdomain.com`) automatically |
| Token stored BEFORE email sent | Email sending is a background task. The token must exist in DB before the background task runs |

### Endpoint 2: `POST /api/users/reset-password`

```python
@router.post("/reset-password", status_code=status.HTTP_200_OK)
async def reset_password(
    body: ResetPasswordRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    token_hash = hash_reset_token(body.token)   # hash what the user sent

    result = await db.execute(
        select(models.PasswordResetToken).where(
            models.PasswordResetToken.token_hash == token_hash,
            models.PasswordResetToken.used.is_(False),       # not already used
        ),
    )
    reset_token = result.scalars().first()

    if not reset_token:
        raise HTTPException(status_code=400, detail="Invalid or expired reset token")

    # Check expiry
    if datetime.now(UTC) > reset_token.expires_at:
        raise HTTPException(status_code=400, detail="Invalid or expired reset token")

    # Update the user's password
    result = await db.execute(
        select(models.User).where(models.User.id == reset_token.user_id),
    )
    user = result.scalars().first()

    user.password_hash = hash_password(body.new_password)
    reset_token.used = True    # mark as used — single-use token
    await db.commit()

    return {"message": "Password reset successful."}
```

**Key decisions:**
- Both "not found" and "expired" return the **same generic error message** (`"Invalid or expired reset token"`) — prevents attackers from knowing whether a token exists or is just expired
- `reset_token.used = True` — marks the token as used so the same link cannot reset the password again
- No need to delete the token row — keeping it `used=True` maintains an audit trail

### Endpoint 3: `POST /api/users/{user_id}/change-password`

```python
@router.post("/{user_id}/change-password", status_code=status.HTTP_200_OK)
async def change_password(
    user_id: int,
    body: ChangePasswordRequest,
    current_user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    if current_user.id != user_id:
        raise HTTPException(status_code=403, detail="Not authorized to change this user's password")

    if not verify_password(body.current_password, current_user.password_hash):
        raise HTTPException(status_code=400, detail="Incorrect current password")

    current_user.password_hash = hash_password(body.new_password)
    await db.commit()

    return {"message": "Password changed successfully."}
```

**Key decisions:**
- Requires `CurrentUser` — the user must be logged in (JWT token)
- Verifies the **current password** before allowing the change — prevents someone with a hijacked session from silently changing the password
- Returns `400` (not `401`) for wrong current password — the user IS authenticated, the password value is just wrong (bad request data)

---

## 9. Updated: `main.py` — Two New Page Routes

```python
@app.get("/forgot-password", include_in_schema=False, name="forgot_password_page")
async def forgot_password_page(request: Request):
    return templates.TemplateResponse(
        request, "forgot_password.html", {"title": "Forgot Password"},
    )

@app.get("/reset-password", include_in_schema=False, name="reset_password_page")
async def reset_password_page(request: Request):
    return templates.TemplateResponse(
        request, "reset_password.html", {"title": "Reset Password"},
    )
```

These are **HTML page routes** (not API routes). They just serve the template — all actual logic is in the JavaScript and the API endpoints.

> **Why `include_in_schema=False`?** These are browser-facing HTML pages, not API endpoints. They shouldn't appear in the OpenAPI docs (`/docs`).

---

## 10. New Templates

### `templates/forgot_password.html`

Simple form asking for an email. The JS always shows a success message on submit (regardless of whether the email exists):

```javascript
// Always shows success message — prevents user enumeration on the frontend too
document.getElementById('successMessage').textContent =
    'If an account with that email exists, a reset link has been sent.';
showModal('successModal');
forgotPasswordForm.reset();
```

### `templates/reset_password.html`

Reads the token from the URL query string, validates passwords match, then POSTs to the API:

```javascript
// Read token from URL: /reset-password?token=abc123
const urlParams = new URLSearchParams(window.location.search);
const token = urlParams.get('token');

// Disable form if no token
if (!token) {
    showModal('errorModal');
    resetPasswordForm.querySelector('button[type="submit"]').disabled = true;
}

// On success, redirect to login after short delay
if (response.ok) {
    showModal('successModal');
    setTimeout(() => { window.location.href = '/login'; }, 2500);
}
```

**Why read the token from the URL (not a hidden form field)?**
The email contains a link like: `https://yourdomain.com/reset-password?token=abc123xyz`
When the user clicks it, the browser opens that URL. The JS reads `?token=` from `window.location.search` and includes it in the API request body.

### `templates/login.html` — Added Link

```html
<p class="mt-1">
    <a href="{{ url_for('forgot_password_page') }}">Forgot your password?</a>
</p>
```

---

## 11. Updated: `account.html` — Working Change Password Form

**Before (Part 13):** Disabled placeholder form with the text "Password reset coming in a future tutorial."

**After (Part 14):** Fully working form with `id="changePasswordForm"`:
```html
<form id="changePasswordForm">
    <input type="password" name="current_password" id="currentPassword" required>
    <input type="password" name="new_password" id="newPassword" required minlength="8">
    <input type="password" name="confirm_new_password" id="confirmNewPassword" required minlength="8">
    <button type="submit">Change Password</button>
</form>
```

JS handler in `account.html`:
```javascript
const changePasswordForm = document.getElementById('changePasswordForm');
changePasswordForm.addEventListener('submit', async (event) => {
    event.preventDefault();

    // Client-side: check new passwords match before sending to server
    if (newPassword !== confirmNewPassword) {
        showModal('errorModal');
        return;
    }

    const response = await fetch(`/api/users/${currentUserId}/change-password`, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
            'Authorization': `Bearer ${token}`,
        },
        body: JSON.stringify({
            current_password: formData.get('current_password'),
            new_password: formData.get('new_password'),
            // confirm_new_password NOT sent — server doesn't need it
        }),
    });
});
```

---

## 12. Key Concepts Explained

### Background Tasks vs Async/Await

```python
# SYNCHRONOUS email (bad — blocks the response):
await send_password_reset_email(user.email, reset_url)  # user waits ~300ms
return {"message": "..."}  # response sent after email

# BACKGROUND TASK (good — instant response):
background_tasks.add_task(send_password_reset_email, user.email, reset_url)
return {"message": "..."}  # response sent NOW, email sent after
```

### Token Security Architecture

```
                    Email sent to user
                    ↓
              raw_token = "gT7x_m8Rz..."    ← 43 chars, random
                    ↓
              DB stores: SHA256("gT7x_m8Rz...") = "a4e1b2c3..."

              If DB is breached:
              Attacker sees "a4e1b2c3..."
              Cannot reverse SHA256 → cannot use token
              ✅ Users are safe
```

### The `sql_delete` Import vs `.delete()` Method
```python
from sqlalchemy import delete as sql_delete

# sql_delete is a bulk DELETE statement — deletes all matching rows in ONE query
await db.execute(
    sql_delete(models.PasswordResetToken).where(
        models.PasswordResetToken.user_id == user.id,
        models.PasswordResetToken.used.is_(False),
    )
)

# db.delete(obj) — deletes ONE specific object you already fetched
# Use sql_delete when you don't need to fetch the objects first (more efficient)
```

### `.is_(False)` vs `== False`
```python
# Wrong (can cause SQLAlchemy warnings with Boolean columns):
models.PasswordResetToken.used == False

# Correct (proper SQLAlchemy IS comparison):
models.PasswordResetToken.used.is_(False)
```
`.is_()` maps to SQL's `IS` operator, which correctly handles Boolean columns and `NULL` comparisons.

### HTTP Status Codes in This Feature

| Code | Meaning | When used |
|------|---------|-----------|
| `200 OK` | Success | Reset password, change password |
| `202 Accepted` | Request received, processing async | Forgot password (email sent in background) |
| `400 Bad Request` | Invalid input data | Wrong current password, invalid/expired token |
| `403 Forbidden` | Authenticated but not authorized | Trying to change another user's password |

### Why the Token Expiry Check is Separate from the DB Query
```python
# The query checks: exists AND not used
# But NOT expiry (hard to do in a DB-agnostic way with timezone-aware datetimes)

# Separate Python check:
if datetime.now(UTC) > reset_token.expires_at:
    raise HTTPException(...)
```
Both invalid/missing and expired tokens return the **same error message** — this is intentional. Giving different messages would let attackers probe: "Ah, this token exists but is expired — I know this user registered."

---

## Summary — What Part 14 Added

```
New file:    email_utils.py          → async SMTP email sender
New model:   PasswordResetToken      → stores hashed tokens in DB
New schemas: ForgotPasswordRequest
             ResetPasswordRequest
             ChangePasswordRequest
New auth:    generate_reset_token()  → cryptographically secure random token
             hash_reset_token()      → SHA-256 hash before DB storage
New config:  reset_token_expire_minutes, mail_server, mail_port,
             mail_username, mail_password, mail_from, extra="ignore"
New routes:  POST /api/users/forgot-password      → trigger reset email
             POST /api/users/reset-password       → apply new password
             POST /api/users/{id}/change-password → change while logged in
             GET  /forgot-password                → HTML page
             GET  /reset-password                 → HTML page
New templates: forgot_password.html
               reset_password.html
Updated:     account.html → working Change Password form
             login.html   → Forgot Password? link
```

---

*Notes by Balaji — covers all changes from Part 13 (Pagination) to Part 14 (Password Reset & Background Tasks).*
