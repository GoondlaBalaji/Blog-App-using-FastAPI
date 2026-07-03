# 7. File Uploads — Image Processing, Validation & Storage

## Overview

This note explains the complete file upload pipeline for user profile pictures, covering every layer of the stack: configuration, image processing utilities, database models, Pydantic schemas, and the API routes that tie it all together.

---

## 1. Configuration (`config.py`)

```python
class Settings(BaseSettings):
    max_upload_size_bytes: int = 5 * 1024 * 1024  # 5 MB
```

A dedicated setting `max_upload_size_bytes` is added to the `Settings` class (loaded from `.env` via `pydantic-settings`). It is defined as `5 * 1024 * 1024` bytes = **5 MB**.

> **Why not hard-code the limit in the route?**  
> By putting it in `Settings`, you can change the limit per environment without touching application code — just set the environment variable.

---

## 2. Image Processing Utilities (`image_utils.py`)

This is a **pure, synchronous** module. No FastAPI, no async — it takes raw bytes, processes them, and saves a file.

```python
PROFILE_PICS_DIR = Path("media/profile_pics")
```

`PROFILE_PICS_DIR` is a `Path` object pointing to `media/profile_pics/`. Using `pathlib.Path` is preferred over raw strings for cross-platform path handling.

---

### `process_profile_image(content: bytes) -> str`

```python
def process_profile_image(content: bytes) -> str:
    with Image.open(BytesIO(content)) as original:
        img = ImageOps.exif_transpose(original)
        img = ImageOps.fit(img, (300, 300), method=Image.Resampling.LANCZOS)
        if img.mode in ("RGBA", "LA", "P"):
            img = img.convert("RGB")
        filename = f"{uuid.uuid4().hex}.jpg"
        filepath = PROFILE_PICS_DIR / filename
        PROFILE_PICS_DIR.mkdir(parents=True, exist_ok=True)
        img.save(filepath, "JPEG", quality=85, optimize=True)
    return filename
```

| Step | Code | Why |
|------|------|-----|
| **1. EXIF Transpose** | `ImageOps.exif_transpose(original)` | Smartphones store rotation in EXIF metadata instead of rotating pixels. Without this, a portrait photo displays sideways. |
| **2. Resize & Crop** | `ImageOps.fit(img, (300, 300), method=Image.Resampling.LANCZOS)` | `fit()` scales so the shortest side matches the target, then center-crops to exactly 300×300. `LANCZOS` is the highest-quality downsampling algorithm. |
| **3. Mode Conversion** | `img.convert("RGB")` | JPEG does **not** support transparency. `RGBA`, `LA`, `P` modes would fail to save as JPEG — converting to `RGB` drops the alpha channel safely. |
| **4. UUID Filename** | `uuid.uuid4().hex` | Random 32-char hex string prevents filename collisions and path traversal attacks. |
| **5. Save** | `img.save(filepath, "JPEG", quality=85, optimize=True)` | `quality=85` is the sweet spot — excellent visuals at ~60-70% of `quality=100` size. `optimize=True` does an extra compression pass. |

**Return value:** Only the filename string (e.g., `"a3f9c123.jpg"`), **not** the full path. The route stores the filename in the DB; the full URL is assembled by the model property.

---

### `delete_profile_image(filename: str | None) -> None`

```python
def delete_profile_image(filename: str | None) -> None:
    if filename is None:
        return
    filepath = PROFILE_PICS_DIR / filename
    if filepath.exists():
        filepath.unlink()
```

1. Returns immediately if `filename` is `None` — no picture to delete.  
2. Calls `.exists()` before `.unlink()` to avoid a `FileNotFoundError` if the file was already removed.

---

## 3. Database Model (`models.py`)

```python
class User(Base):
    image_file: Mapped[str | None] = mapped_column(
        String(200),
        nullable=True,
        default=None,
    )

    @property
    def image_path(self) -> str:
        if self.image_file:
            return f"/media/profile_pics/{self.image_file}"
        return "/static/profile_pics/default.jpg"
```

**`nullable=True, default=None`:** New users have no picture. Only the **filename** is stored (e.g., `"a3f9c123.jpg"`), not the full URL. If you move to a CDN later, only the `image_path` property changes — not the stored data.

**`@property image_path`:** A computed Python property (not SQL). Templates and schemas access `user.image_path` and always get a usable URL. If no picture, it falls back to the static default placeholder. Clients never construct paths themselves.

---

## 4. Pydantic Schemas (`schemas.py`)

```python
class UserPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    username: str
    image_file: str | None   # raw filename or None
    image_path: str          # the computed, usable URL

class UserUpdate(BaseModel):
    username: str | None = Field(default=None, min_length=1, max_length=50)
    email: EmailStr | None = Field(default=None, max_length=120)
    # NOTE: image_file is intentionally NOT here
```

**Why is `image_file` excluded from `UserUpdate`?**  
Allowing clients to PATCH an arbitrary filename would be a security hole — a user could point their picture to another user's file. By removing it, the **only** way to change a profile picture is through the dedicated, validated `/picture` endpoint.

`from_attributes=True` (formerly `orm_mode`) lets Pydantic read directly from SQLAlchemy ORM objects, including the computed `@property` `image_path`.

---

## 5. API Routes (`routers/users.py`)

### PATCH `/{user_id}/picture` — Upload Profile Picture

```python
@router.patch("/{user_id}/picture", response_model=UserPrivate)
async def upload_profile_picture(
    user_id: int,
    file: UploadFile,           # multipart/form-data file
    current_user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    # 1. Authorization
    if current_user.id != user_id:
        raise HTTPException(status_code=403, detail="Not authorized...")

    # 2. Read file into memory
    content = await file.read()

    # 3. Size validation
    if len(content) > settings.max_upload_size_bytes:
        raise HTTPException(status_code=400, detail=f"File too large. Maximum size is {settings.max_upload_size_bytes // (1024 * 1024)}MB")

    # 4. Process image in a thread (non-blocking)
    try:
        new_filename = await run_in_threadpool(process_profile_image, content)
    except UnidentifiedImageError as err:
        raise HTTPException(status_code=400, detail="Invalid image file...") from err

    # 5. Save to DB, then clean up old file
    old_filename = current_user.image_file
    current_user.image_file = new_filename
    await db.commit()
    await db.refresh(current_user)
    if old_filename:
        delete_profile_image(old_filename)

    return current_user
```

**Step-by-step deep dive:**

**Step 1 — Authorization first:** We check auth **before** any I/O. Unauthorized requests fail fast without wasting time reading or processing the file.

**Step 2 — `UploadFile` & `.read()`:** FastAPI's `UploadFile` wraps the incoming multipart stream. `await file.read()` reads the entire body into memory as `bytes`. This is fine for images; for large files (video), streaming to disk would be better.

**Step 3 — Size check:** We check size **after** reading because `Content-Length` headers can be spoofed. The error message dynamically shows the limit in MB using integer division: `5 * 1024 * 1024 // (1024 * 1024) = 5`.

**Step 4 — `run_in_threadpool` (Critical):**

```
FastAPI's async event loop is single-threaded.
Pillow (image processing) is CPU-bound and blocking.

If we called process_profile_image() directly,
Python cannot context-switch during CPU work —
the entire server freezes until the image is done.

run_in_threadpool() runs the function in a separate OS thread,
freeing the event loop to handle other requests while Pillow works.
```

`UnidentifiedImageError` fires when Pillow cannot recognize the file format (e.g., a `.txt` renamed to `.jpg`). We catch it and return a clean 400.

**Step 5 — Safe ordering:** New filename is committed to DB **before** deleting the old file:
- If DB commit fails → no file deleted (old picture stays intact).
- If file deletion fails → DB is still consistent (new picture is saved).

---

### DELETE `/{user_id}/picture` — Remove Profile Picture

```python
@router.delete("/{user_id}/picture", response_model=UserPrivate)
async def delete_user_picture(...):
    old_filename = current_user.image_file
    if old_filename is None:
        raise HTTPException(status_code=400, detail="No profile picture to delete")

    current_user.image_file = None
    await db.commit()
    await db.refresh(current_user)
    delete_profile_image(old_filename)
    return current_user
```

Guard against deleting when there is no picture. DB commit happens before file deletion — same safe ordering as upload.

---

### DELETE `/{user_id}` — Account Deletion Cleanup

```python
old_filename = user.image_file
await db.delete(user)
await db.commit()
if old_filename:
    delete_profile_image(old_filename)
```

When an account is deleted, the profile picture is cleaned up too. Without this, deleted users leave **orphaned files** on disk forever.

---

## 6. Static File Serving (`main.py`)

```python
app.mount("/static", StaticFiles(directory="static"), name="static")
app.mount("/media",  StaticFiles(directory="media"),  name="media")
```

Two mounts serve two purposes:
- `/static` → bundled assets (CSS, JS, `default.jpg` placeholder)
- `/media` → user-uploaded content (profile pictures)

Separating them lets you apply different caching policies (e.g., `/static` is aggressively cached; `/media` may need shorter TTLs since content changes).

---

## Summary — The Full Upload Flow

```
Client: PATCH /api/users/2/picture  (multipart/form-data)
    │
    ├─ Auth check: current_user.id == 2?  No → 403
    │
    ├─ await file.read() → content (bytes)
    │
    ├─ len(content) > 5MB?  Yes → 400
    │
    ├─ run_in_threadpool(process_profile_image, content)
    │      ├─ EXIF rotation fix
    │      ├─ 300×300 center-crop (LANCZOS)
    │      ├─ RGBA/P → RGB conversion
    │      └─ Save as JPEG (quality=85) → "abc123.jpg"
    │   UnidentifiedImageError → 400
    │
    ├─ user.image_file = "abc123.jpg" → db.commit()
    │
    ├─ delete_profile_image(old_filename)
    │
    └─ Return UserPrivate (image_path = "/media/profile_pics/abc123.jpg")
```
