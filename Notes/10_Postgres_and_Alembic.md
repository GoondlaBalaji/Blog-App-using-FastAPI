# 10. PostgreSQL and Alembic — Database Migrations for Production

> **What changed:** This note covers transitioning the application from a local SQLite development database to a production-ready **PostgreSQL** database, and setting up **Alembic** to manage database schema migrations instead of letting SQLAlchemy create tables on startup.

---

## Table of Contents

1. [Why Move from SQLite to PostgreSQL?](#1-why-move-from-sqlite-to-postgresql)
2. [Why Do We Need Alembic (Migrations)?](#2-why-do-we-need-alembic-migrations)
3. [Configuration Updated: `config.py` & `.env`](#3-configuration-updated-configpy--env)
4. [Engine Updated: `database.py`](#4-engine-updated-databasepy)
5. [Startup Refactored: `main.py`](#5-startup-refactored-mainpy)
6. [Alembic Setup & env.py Configuration](#6-alembic-setup--envpy-configuration)
7. [Windows-Specific Issue: Psycopg and ProactorEventLoop](#7-windows-specific-issue-psycopg-and-proactoreventloop)
8. [Standard Migration Workflow & Commands](#8-standard-migration-workflow--commands)
9. [How Autogenerate Works Under the Hood](#9-how-autogenerate-works-under-the-hood)

---

## 1. Why Move from SQLite to PostgreSQL?

| Feature | SQLite | PostgreSQL |
| :--- | :--- | :--- |
| **Concurrency** | Single-writer lock (writes block other writes) | Multi-Version Concurrency Control (MVCC) — high concurrent read/write |
| **Data Types** | Weakly typed (stores anything in any column) | Strict typing, advanced types (JSONB, UUID, Arrays) |
| **Scaling** | Local file only (fails in serverless/containerised setups) | Can run on dedicated RDS/Cloud DB instances, supporting multiple app containers |
| **Production Ready** | ❌ No (dev/testing only) | ✅ Yes |

---

## 2. Why Do We Need Alembic (Migrations)?

In early tutorials, we created tables by running:
```python
async with engine.begin() as conn:
    await conn.run_sync(Base.metadata.create_all)
```

### The Problem with `create_all`:
1. It only creates tables **if they do not already exist**.
2. If you add a new column (e.g., adding `likes` to `Post`), `create_all` does **nothing** because the `posts` table already exists. You would have to drop the entire table (deleting all data) to add the column.
3. This is unacceptable in production because dropping tables deletes real user data.

### The Alembic Solution:
Alembic tracks versioned scripts (e.g., `9c989cb6162f_initial_schema.py`) that tell the database how to alter tables step-by-step:
- **`upgrade()`**: Adds columns/tables.
- **`downgrade()`**: Reverts changes.

---

## 3. Configuration Updated: `config.py` & `.env`

We added `database_url` to Settings so database engines can load dynamically based on the environment:

```python
# config.py
class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",  # ignores other environment variables like MAIL_USE_TLS
    )
    
    database_url: str  # Loaded dynamically from .env
```

In `.env`, we specify the PostgreSQL async connection string:
```env
DATABASE_URL=postgresql+psycopg://postgres:pass123@localhost/fastapi_blog_db
```
*(Note: We use the `psycopg` driver for async PostgreSQL connections, matching SQLAlchemy standards).*

---

## 4. Engine Updated: `database.py`

### SQLite Engine (Old)
```python
SQLALCHEMY_DATABASE_URL = "sqlite+aiosqlite:///./blog.db"

engine = create_async_engine(
    SQLALCHEMY_DATABASE_URL,
    connect_args={"check_same_thread": False},  # SQLite specific
)
```

### PostgreSQL Engine (New)
```python
from config import settings

engine = create_async_engine(settings.database_url)
```
- We read `settings.database_url` directly.
- We **removed** `connect_args={"check_same_thread": False}` because it is an SQLite-specific parameter to allow multiple threads to access the local DB. PostgreSQL handles connection pooling natively.

---

## 5. Startup Refactored: `main.py`

In `main.py`, the `lifespan` startup hook was cleaned up:

```python
# BEFORE (Tables created on app startup)
@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Startup
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    # Shutdown
    await engine.dispose()

# AFTER (Database schema is now managed entirely by Alembic)
@asynccontextmanager
async def lifespan(_app: FastAPI):
    yield
    # Shutdown
    await engine.dispose()
```

---

## 6. Alembic Setup & env.py Configuration

After running `alembic init -t async alembic` to initialize a template for async migrations, we configured `alembic/env.py` to bridge Alembic with our application settings and models:

```python
# alembic/env.py
from config import settings
from database import Base
import models  # must import models to let Alembic auto-detect schemas

# Set the URL dynamically from config settings instead of hardcoding in alembic.ini
config = context.config
config.set_main_option("sqlalchemy.url", settings.database_url)

# Define metadata for autogenerate support
target_metadata = Base.metadata
```

---

## 7. Windows-Specific Issue: Psycopg and ProactorEventLoop

On Windows systems running Python 3.8+, the default async loop is `ProactorEventLoop`. However, `psycopg` (PostgreSQL async driver) does not support it and throws:
`psycopg.InterfaceError: Psycopg cannot use the 'ProactorEventLoop' to run in async mode.`

### Solution
We configured `alembic/env.py` to switch to `SelectorEventLoop` on Windows by setting the event loop policy:

```python
import sys
import asyncio

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
```

---

## 8. Standard Migration Workflow & Commands

Whenever you change your models (e.g. adding a new table or column in `models.py`), you follow these steps:

### A. Autogenerate a Migration Script
Compare your `models.py` metadata with the live database and generate a python migration:
```bash
alembic revision --autogenerate -m "describe changes"
```
This generates a file under `alembic/versions/` (e.g. `9c989cb6162f_initial_schema.py`).

### B. Apply the Migration to the Database
Run the generated script to execute SQL DDL changes on your database:
```bash
alembic upgrade head
```

### Other Useful Commands:
- **`alembic current`**: View the current revision status of the database.
- **`alembic history`**: View all historical migration steps chronologically.
- **`alembic downgrade -1`**: Revert the last applied migration.

---

## 9. How Autogenerate Works Under the Hood

When you run `--autogenerate`, Alembic connects to your target database, reads its catalog, and compares it to `Base.metadata` (from your imported `models.py`):

```
                   Alembic compares:
  [ models.py ]                        [ database catalog ]
  (Base.metadata)                      (active tables/indexes)
         │                                       │
         └───────────────────┬───────────────────┘
                             ▼
                  Generates script to match:
         - Create missing tables/columns
         - Drop unused tables/columns
         - Alter modified constraints
```

> ⚠️ **Caution:** Always review the generated script under `alembic/versions/` before running `upgrade head`! Autogenerate can miss complex changes (like column renames) and might generate `drop` statements for unrelated tables if you share a database catalog (which is why we created a dedicated `fastapi_blog_db` database).

---

*Notes by Balaji — covers all changes from Part 14 (Password Reset) to Part 15 (PostgreSQL & Alembic).*
