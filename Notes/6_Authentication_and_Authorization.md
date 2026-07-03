# 6. Authentication & Authorization in FastAPI

> **What changed:** This note covers everything added on top of the GitHub repo
> to implement full JWT-based Authentication & Authorization.

---

## Table of Contents

1. [What is Authentication vs Authorization?](#1-what-is-authentication-vs-authorization)
2. [New File: `auth.py`](#2-new-file-authpy)
3. [New File: `config.py`](#3-new-file-configpy)
4. [Schemas Updated: `schemas.py`](#4-schemas-updated-schemaspy)
5. [Models Updated: `models.py`](#5-models-updated-modelspy)
6. [Router Updated: `routers/users.py`](#6-router-updated-routersuserspy)
7. [Router Updated: `routers/posts.py`](#7-router-updated-routerspostspy)
8. [Main Updated: `main.py`](#8-main-updated-mainpy)
9. [The Full Auth Flow — Step by Step](#9-the-full-auth-flow--step-by-step)
10. [Key Concepts Explained](#10-key-concepts-explained)

---

## 1. What is Authentication vs Authorization?

| Term | Meaning | Example |
|---|---|---|
| **Authentication** | *Who are you?* Proving your identity | Logging in with email + password |
| **Authorization** | *What are you allowed to do?* | Only the post owner can delete their post |

In this project:
- **Authentication** = JWT tokens issued on login
- **Authorization** = checking `current_user.id` matches the resource owner before allowing updates/deletes

---

## 2. New File: `auth.py`

This entire file was **not in the GitHub repo**. It is the heart of the auth system.

```python
from datetime import UTC, datetime, timedelta
from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from pwdlib import PasswordHash
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import models
from config import settings
from database import get_db
```

### Key components inside `auth.py`

#### A) Password Hashing
```python
password_hash = PasswordHash.recommended()

def hash_password(password: str) -> str:
    return password_hash.hash(password)

def verify_password(plain_password: str, hashed_password: str) -> bool:
    return password_hash.verify(plain_password, hashed_password)
```
- `pwdlib` is used (modern replacement for `passlib`)
- `PasswordHash.recommended()` picks the best algorithm (Argon2/bcrypt)
- **Never store plain-text passwords** — always store the hash

#### B) OAuth2 Scheme
```python
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="api/users/token")
```
- Tells FastAPI where the login endpoint is (`/api/users/token`)
- This makes Swagger UI show an "Authorize" button automatically
- Extracts the Bearer token from the `Authorization` header on every request

#### C) Creating JWT Tokens
```python
def create_access_token(data: dict, expires_delta: timedelta | None = None) -> str:
    to_encode = data.copy()
    expire = datetime.now(UTC) + (expires_delta or timedelta(minutes=settings.access_token_expire_minutes))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, settings.secret_key.get_secret_value(), algorithm=settings.algorithm)
```
- `data` will be `{"sub": str(user.id)}` — the user's ID is stored in the token
- `exp` = expiry time (e.g. 30 minutes from now)
- The token is signed with your `SECRET_KEY` from `.env`
- **The token is NOT encrypted**, but it IS signed — tampering is detectable

#### D) Verifying JWT Tokens
```python
def verify_access_token(token: str) -> str | None:
    try:
        payload = jwt.decode(
            token,
            settings.secret_key.get_secret_value(),
            algorithms=[settings.algorithm],
            options={"require": ["exp", "sub"]},
        )
    except jwt.InvalidTokenError:
        return None
    return payload.get("sub")
```
- Decodes and validates the token signature
- `options={"require": ["exp", "sub"]}` — forces the token to have both expiry and subject
- Returns the `sub` (user id string) or `None` if invalid/expired

#### E) `get_current_user` Dependency
```python
async def get_current_user(
    token: Annotated[str, Depends(oauth2_scheme)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> models.User:
    user_id = verify_access_token(token)
    if user_id is None:
        raise HTTPException(status_code=401, detail="Invalid or expired token",
                            headers={"WWW-Authenticate": "Bearer"})
    user_id_int = int(user_id)
    result = await db.execute(select(models.User).where(models.User.id == user_id_int))
    user = result.scalars().first()
    if not user:
        raise HTTPException(status_code=401, detail="User not found",
                            headers={"WWW-Authenticate": "Bearer"})
    return user
```
- This is a **FastAPI dependency** — inject it into any route to protect it
- It reads the Bearer token → verifies it → looks up the user in DB → returns the user object
- If anything fails, it raises a `401 Unauthorized` error automatically

#### F) `CurrentUser` Type Alias
```python
CurrentUser = Annotated[models.User, Depends(get_current_user)]
```
- A reusable shorthand so you don't repeat `Annotated[models.User, Depends(get_current_user)]` everywhere
- Usage in any route: just add `current_user: CurrentUser` as a parameter

---

## 3. New File: `config.py`

Also **not in the GitHub repo**. Manages environment variables securely.

```python
from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    secret_key: SecretStr      # Loaded from .env
    algorithm: str = "HS256"   # JWT signing algorithm
    access_token_expire_minutes: int = 30

settings = Settings()
```

Your `.env` file must have:
```
SECRET_KEY=your-very-long-random-secret-key-here
ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=30
```

- `SecretStr` prevents the key from being accidentally printed/logged
- `pydantic_settings` auto-loads values from `.env`

> **Why HS256?** It's a symmetric algorithm — same key is used to sign and verify.
> Good for single-server apps. For multi-server, RS256 (asymmetric) is better.

---

## 4. Schemas Updated: `schemas.py`

### GitHub version (old):
```python
class UserCreate(UserBase):
    pass  # No password field!

class UserResponse(UserBase):
    id: int
    image_file: str | None

class PostCreate(PostBase):
    user_id: int  # TEMPORARY — client had to send their own user ID (insecure!)
```

### Local version (new):
```python
# UserCreate now requires a password
class UserCreate(UserBase):
    password: str = Field(min_length=8)

# Split into public and private views of a user
class UserPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    username: str
    image_file: str | None
    image_path: str           # computed property from the model

class UserPrivate(UserPublic):
    email: EmailStr           # email only visible to the user themselves

# Token schema for the login response
class Token(BaseModel):
    access_token: str
    token_type: str

# PostCreate no longer needs user_id (comes from token now)
class PostCreate(PostBase):
    pass
```

**Why `UserPublic` vs `UserPrivate`?**
- `UserPublic` — shown when listing posts (author info, no email)
- `UserPrivate` — shown to the logged-in user for their own account (includes email)

---

## 5. Models Updated: `models.py`

### Added to the `User` model:
```python
password_hash: Mapped[str] = mapped_column(String(200), nullable=False)
image_file: Mapped[str | None] = mapped_column(String(200), nullable=True, default=None)

@property
def image_path(self) -> str:
    if self.image_file:
        return f"/media/profile_pics/{self.image_file}"
    return "/static/profile_pics/default.jpg"
```
- `password_hash` stores the hashed password (never plain text)
- `image_path` is a computed property — returns the URL path for the profile picture
- The `image_path` field in `UserPublic` schema reads from this property

---

## 6. Router Updated: `routers/users.py`

### What was added vs GitHub:

#### A) New imports
```python
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import func, select  # func added for case-insensitive queries
from auth import CurrentUser, create_access_token, hash_password, verify_password
from config import settings
from schemas import PostResponse, Token, UserCreate, UserPrivate, UserPublic, UserUpdate
```

#### B) `create_user` — now hashes password + case-insensitive checks
```python
async def create_user(user: UserCreate, db: ...):
    # Check username (case-insensitive) using func.lower()
    result = await db.execute(
        select(models.User).where(func.lower(models.User.username) == user.username.lower())
    )
    # Check email (case-insensitive)
    result = await db.execute(
        select(models.User).where(func.lower(models.User.email) == user.email.lower())
    )
    # Hash password before saving — NEVER store plain text
    new_user = models.User(
        username=user.username,
        email=user.email.lower(),
        password_hash=hash_password(user.password),
    )
```

#### C) New: `/token` endpoint — the Login Route
```python
@router.post("/token", response_model=Token)
async def login_for_access_token(
    form_data: Annotated[OAuth2PasswordRequestForm, Depends()],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    # Find user by email (OAuth2 form uses "username" field but we treat it as email)
    result = await db.execute(
        select(models.User).where(func.lower(models.User.email) == form_data.username.lower())
    )
    user = result.scalars().first()

    # Verify credentials — don't reveal which one failed (security best practice)
    if not user or not verify_password(form_data.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Incorrect email or password")

    # Issue JWT token
    access_token = create_access_token(
        data={"sub": str(user.id)},
        expires_delta=timedelta(minutes=settings.access_token_expire_minutes)
    )
    return Token(access_token=access_token, token_type="bearer")
```

> `OAuth2PasswordRequestForm` sends data as form fields (not JSON).
> Its field is called `username` but we use it as the email field.

#### D) New: `/me` endpoint
```python
@router.get("/me", response_model=UserPrivate)
async def get_current_user(current_user: CurrentUser):
    return current_user
```
- Requires a valid token
- Returns the logged-in user's full private info (including email)

#### E) New: `PATCH /{user_id}` — Update user with auth
```python
@router.patch("/{user_id}", response_model=UserPrivate)
async def update_user(user_id: int, user_update: UserUpdate, current_user: CurrentUser, db: ...):
    if user_id != current_user.id:
        raise HTTPException(status_code=403, detail="You are not authorized to update this user")
    # ... apply updates with duplicate checks
```

#### F) New: `DELETE /{user_id}` — Delete user with auth
```python
@router.delete("/{user_id}", status_code=204)
async def delete_user(user_id: int, current_user: CurrentUser, db: ...):
    if user_id != current_user.id:
        raise HTTPException(status_code=403, detail="You are not authorized to delete this user")
    # ... delete user
```

---

## 7. Router Updated: `routers/posts.py`

### What changed:

| Route | GitHub (old) | Local (new) |
|---|---|---|
| `POST /` | No auth, required `user_id` in body | ✅ `CurrentUser` — gets `user_id` from token |
| `PUT /{id}` | No auth, compared `post_data.user_id` | ✅ `CurrentUser` — compares `current_user.id` |
| `PATCH /{id}` | Had `CurrentUser` but no 403 check | ✅ Added proper 403 Forbidden check |
| `DELETE /{id}` | ✅ Already correct | ✅ Still correct |

#### `create_post` — before vs after
```python
# BEFORE (GitHub): user had to send their own ID in the body (anyone could spoof it!)
async def create_post(post: PostCreate, db: ...):
    new_post = models.Post(title=post.title, content=post.content, user_id=post.user_id)

# AFTER (Local): user ID comes from the verified JWT token
async def create_post(post: PostCreate, current_user: CurrentUser, db: ...):
    new_post = models.Post(title=post.title, content=post.content, user_id=current_user.id)
```

#### `update_post_full` (PUT) — before vs after
```python
# BEFORE: compared with user_id in the request body (insecure — anyone could fake it!)
if post.user_id != post_data.user_id:
    raise HTTPException(status_code=403, ...)

# AFTER: compares with the authenticated user from the token
async def update_post_full(post_id, post_data, current_user: CurrentUser, db):
    if post.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="You are not authorized to update this post")
    post.title = post_data.title
    post.content = post_data.content
    # Note: post.user_id is NOT changed (ownership stays with original author)
```

#### `update_post_partial` (PATCH) — added missing auth check
```python
# Added (was completely missing before):
if post.user_id != current_user.id:
    raise HTTPException(status_code=403, detail="You are not authorized to update this post")
```

---

## 8. Main Updated: `main.py`

### Changes from GitHub:

1. **Added `lifespan` parameter** to `FastAPI()` for proper startup/shutdown:
   ```python
   app = FastAPI(lifespan=lifespan)  # GitHub had: app = FastAPI() without lifespan
   ```

2. **Added `/account` route** (was completely missing — caused 500 error on every page):
   ```python
   @app.get("/account", include_in_schema=False, name="account_page")
   async def account_page(request: Request):
       return templates.TemplateResponse(request, "account.html", {"title": "Account"})
   ```
   The `layout.html` template called `url_for("account_page")` on every page, so without this route every single page was crashing with a 500 Internal Server Error.

3. **Added custom exception handlers** for better error pages:
   ```python
   @app.exception_handler(StarletteHTTPException)
   async def general_http_exception_handler(request, exception):
       if request.url.path.startswith("/api"):
           return await http_exception_handler(request, exception)  # Return JSON for API
       return templates.TemplateResponse(request, "error.html", {...})  # Return HTML for browser
   ```
   This means API errors return JSON, browser errors return a nice error page.

4. **Added all template page routes** for `/`, `/posts`, `/posts/{id}`, `/users/{id}/posts`, `/login`, `/register`, `/account`.

---

## 9. The Full Auth Flow — Step by Step

```
1. USER REGISTERS
   POST /api/users
   Body: { username, email, password }
         ↓
   Server hashes password with pwdlib → stores in DB
   Returns: UserPrivate (id, username, email, image_file)

2. USER LOGS IN
   POST /api/users/token
   Form: { username=email@example.com, password=mypassword }
         ↓
   Server looks up user by email (case-insensitive)
   Server verifies password against stored hash
   Server creates JWT: payload = { sub: "5", exp: <30 min from now> }
   JWT is signed with SECRET_KEY
   Returns: { access_token: "eyJ...", token_type: "bearer" }

3. USER MAKES AUTHENTICATED REQUEST
   POST /api/posts
   Header: Authorization: Bearer eyJ...
   Body: { title: "Hello", content: "World" }
         ↓
   oauth2_scheme extracts "eyJ..." from the Authorization header
   get_current_user() dependency is called:
     → verify_access_token("eyJ...") → decodes JWT → returns "5" (user_id string)
     → DB query: SELECT * FROM users WHERE id = 5
     → returns User object as current_user
   Route runs: new_post.user_id = current_user.id  (= 5)

4. USER TRIES TO DELETE SOMEONE ELSE'S POST
   DELETE /api/posts/10
   Header: Authorization: Bearer eyJ... (belongs to user id=2)
         ↓
   post = DB query returns Post(id=10, user_id=1)
   current_user.id = 2
   post.user_id (1) != current_user.id (2)
   → 403 Forbidden: "You are not authorized to delete this post"
```

---

## 10. Key Concepts Explained

### JWT (JSON Web Token)
A JWT has 3 parts separated by dots: `header.payload.signature`
- **Header**: algorithm type (`{"alg": "HS256", "typ": "JWT"}`)
- **Payload**: your data (`{"sub": "5", "exp": 1234567890}`)
- **Signature**: HMAC-SHA256 of Base64(header) + "." + Base64(payload) using SECRET_KEY

The token is **Base64 encoded**, not encrypted. Anyone can decode and read the payload. But they **cannot forge** a valid signature without the SECRET_KEY — so tampering is detectable.

### `Depends()` — FastAPI's Dependency Injection
```python
# This route REQUIRES a valid JWT. If not present/valid, FastAPI returns 401 automatically.
async def create_post(post: PostCreate, current_user: CurrentUser, db: ...):
    ...
```
FastAPI resolves `CurrentUser` → calls `get_current_user()` → which calls `verify_access_token()`.
All of this happens automatically **before** your route function body runs.

### HTTP Status Codes Used in Auth
| Code | Meaning | When used |
|---|---|---|
| `200` | OK | GET requests |
| `201` | Created | POST (create user/post) |
| `204` | No Content | DELETE |
| `400` | Bad Request | Username/email already exists |
| `401` | Unauthorized | No token / invalid / expired token |
| `403` | Forbidden | Valid token, but wrong user (not owner) |
| `404` | Not Found | Post/User doesn't exist |

> **401 vs 403:** 401 = "I don't know who you are." 403 = "I know who you are, but you can't do this."

### `WWW-Authenticate: Bearer` Header
When returning a `401`, always include `headers={"WWW-Authenticate": "Bearer"}`.
This is part of the OAuth2 spec and tells the client:
> "This endpoint requires a Bearer token in the Authorization header."

### Why store `user_id` as `sub` in the token?
```python
data={"sub": str(user.id)}  # sub = "subject" — who the token is about
```
- `sub` is a standard JWT claim (RFC 7519)
- We store the user's **integer ID** (converted to string, as JWT sub must be a string)
- IDs never change, but usernames/emails can — so IDs are safer as the token subject

### `func.lower()` for Case-Insensitive Queries
```python
select(models.User).where(func.lower(models.User.email) == user.email.lower())
```
- `func.lower()` calls SQL's `LOWER()` function on the database column
- `.lower()` on the Python side lowercases the input value
- This ensures `"John@Gmail.com"` and `"john@gmail.com"` are treated as the same email

---

*Notes by Balaji — covers all changes from GitHub base to local auth implementation.*
