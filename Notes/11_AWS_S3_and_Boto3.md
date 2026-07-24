# 11. AWS S3 and Boto3 — Moving File Uploads to the Cloud

> **What changed:** This note covers migrating profile image storage from the local disk (`media/profile_pics/`) to **Amazon S3** (or any S3-compatible service) using `boto3`. We offload synchronous S3 calls to a background thread pool to ensure our FastAPI app remains fast and responsive.

---

## Table of Contents

1. [Why Move to Cloud Object Storage?](#1-why-move-to-cloud-object-storage)
2. [How the Async S3 Upload Flow Works](#2-how-the-async-s3-upload-flow-works)
3. [Configuration Updated: `config.py` & `.env`](#3-configuration-updated-configpy--env)
4. [User Model Updated: `models.py`](#4-user-model-updated-modelspy)
5. [Refactored: `image_utils.py` (In-Memory Processing + S3)](#5-refactored-image_utilspy-in-memory-processing--s3)
6. [Updated: `routers/users.py` (Async Upload & Deletion)](#6-updated-routersuserspy-async-upload--deletion)
7. [Testing locally with LocalStack / MinIO](#7-testing-locally-with-localstack--minio)

---

## 1. Why Move to Cloud Object Storage?

| Local Storage | AWS S3 |
| :--- | :--- |
| ❌ Files are deleted when container/server restarts. | ✅ Files persist permanently (99.999999999% durability). |
| ❌ Cannot scale horizontally (Server B cannot see files on Server A). | ✅ Centralised bucket accessible by all server instances. |
| ❌ Consumes server disk space and bandwidth. | ✅ Frees up web server resources; users download directly from S3. |

---

## 2. How the Async S3 Upload Flow Works

`boto3` is a synchronous / blocking library. In FastAPI, running blocking operations directly freezes the event loop.

To prevent this, we run S3 upload and delete tasks in a separate background thread pool using Starlette's `run_in_threadpool`.

```
  HTTP Request (PATCH /api/users/me/picture)
       │
       ▼
  Read file into memory buffer (BytesIO)
       │
       ▼
  process_profile_image()  ──► Resizes & formats image in memory
       │
       ▼
  run_in_threadpool(upload_to_s3) ──► Runs blocking boto3 upload on background worker thread
       │
       ▼
  FastAPI continues serving other users concurrently
```

---

## 3. Configuration Updated: `config.py` & `.env`

Added five S3 environment configurations to Pydantic settings:

```python
# config.py
class Settings(BaseSettings):
    ...
    # S3 Configuration
    s3_bucket_name: str
    s3_region: str = "us-east-1"
    s3_access_key_id: SecretStr | None = None
    s3_secret_access_key: SecretStr | None = None
    s3_endpoint_url: str | None = None
```

In `.env`, we specify our AWS credentials and target S3 bucket:
```env
S3_BUCKET_NAME=fastapi-blog-uploads-profiles
S3_REGION=ap-south-1
S3_ACCESS_KEY_ID=AKIA...
S3_SECRET_ACCESS_KEY=eMeC...
```

---

## 4. User Model Updated: `models.py`

The `image_path` property was updated to point directly to the public S3 URL endpoint instead of a local media prefix:

```python
# models.py
@property
def image_path(self) -> str:
    if self.image_file:
        return f"https://{settings.s3_bucket_name}.s3.{settings.s3_region}.amazonaws.com/profile_pics/{self.image_file}"
    return "/static/profile_pics/default.jpg"
```

---

## 5. Refactored: `image_utils.py` (In-Memory Processing + S3)

Instead of saving processed JPEG files to the disk, `process_profile_image` saves the output to a `BytesIO` buffer in memory:

```python
# image_utils.py
import uuid
from io import BytesIO
import boto3
from PIL import Image, ImageOps
from starlette.concurrency import run_in_threadpool
from config import settings

def _get_s3_client():
    return boto3.client(
        "s3",
        region_name=settings.s3_region,
        aws_access_key_id=settings.s3_access_key_id.get_secret_value() if settings.s3_access_key_id else None,
        aws_secret_access_key=settings.s3_secret_access_key.get_secret_value() if settings.s3_secret_access_key else None,
        endpoint_url=settings.s3_endpoint_url,
    )

def process_profile_image(content: bytes) -> tuple[bytes, str]:
    with Image.open(BytesIO(content)) as original:
        img = ImageOps.exif_transpose(original)
        img = ImageOps.fit(img, (300, 300), method=Image.Resampling.LANCZOS)
        
        if img.mode in ("RGBA", "LA", "P"):
            img = img.convert("RGB")
            
        filename = f"{uuid.uuid4().hex}.jpg"
        output = BytesIO()
        img.save(output, "JPEG", quality=85, optimize=True)
        output.seek(0)
        
    return output.read(), filename

def _upload_to_s3(file_bytes: bytes, key: str) -> None:
    s3 = _get_s3_client()
    s3.upload_fileobj(
        BytesIO(file_bytes),
        settings.s3_bucket_name,
        key,
        ExtraArgs={"ContentType": "image/jpeg"},
    )

async def upload_profile_image(file_bytes: bytes, filename: str) -> None:
    key = f"profile_pics/{filename}"
    await run_in_threadpool(_upload_to_s3, file_bytes, key)
```

---

## 6. Updated: `routers/users.py` (Async Upload & Deletion)

We update routes to upload to S3 and catch `ClientError` if uploads fail:

```python
# routers/users.py
from botocore.exceptions import ClientError
from image_utils import delete_profile_image, process_profile_image, upload_profile_image

@router.patch("/{user_id}/picture", response_model=UserPrivate)
async def upload_profile_picture(user_id: int, file: UploadFile, current_user: CurrentUser, db: ...):
    # ... checks ...
    content = await file.read()
    
    processed_bytes, new_filename = await run_in_threadpool(process_profile_image, content)
    
    # Upload to S3 asynchronously
    try:
        await upload_profile_image(processed_bytes, new_filename)
    except ClientError as err:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to upload image. Please try again.",
        ) from err

    old_filename = current_user.image_file
    current_user.image_file = new_filename
    await db.commit()
    
    # Async clean up of old image on S3
    if old_filename:
        await delete_profile_image(old_filename)

    return current_user
```

---

## 7. Testing locally with LocalStack / MinIO

During development, you can mock AWS S3 without paying for AWS:
1. Run LocalStack or MinIO locally on port `4566` / `9000`.
2. Update your `.env` to include:
   ```env
   S3_ENDPOINT_URL=http://localhost:4566
   ```
The `boto3` client will automatically redirect all read/write requests to your local mock environment!

---

*Notes by Balaji — covers all changes from Part 15 (PostgreSQL & Alembic) to Part 16 (AWS S3 & Boto3).*
