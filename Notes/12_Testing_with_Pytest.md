# 12. Automated Testing with Pytest

> **What changed:** This note covers setting up an automated testing suite using `pytest`, `pytest-asyncio`, and `httpx`. We used an isolated **in-memory SQLite database** to ensure tests run rapidly and do not corrupt the primary PostgreSQL database.

---

## 1. Core Testing Libraries

- **`pytest`**: The framework used for discovering and running the tests.
- **`pytest-asyncio`**: Required to run `async` test functions because FastAPI relies heavily on `async/await`.
- **`httpx`**: Replaces the standard `requests` library to make asynchronous HTTP requests against the FastAPI app via `AsyncClient`.
- **`moto`**: Used to mock AWS services, allowing us to safely test S3 uploads without touching the real AWS bucket.

---

## 2. Test Environment Setup (`conftest.py`)

To ensure tests are isolated, we configure an **in-memory SQLite database**:

```python
# conftest.py
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"
```

### Database Isolation (Resetting Per Test)

We configure the `setup_database` fixture with **function-level scope** (the default in pytest). This ensures the test database schema is dropped and recreated for every single test function, keeping them 100% isolated:

```python
@pytest.fixture
async def setup_database(test_engine):
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
```

To prevent the in-memory SQLite database from being lost between these queries, we use a `StaticPool`:
```python
from sqlalchemy.pool import StaticPool

engine = create_async_engine(
    os.environ["DATABASE_URL"],
    poolclass=StaticPool,
    connect_args={"check_same_thread": False},
)
```

### Dependency Overrides

In FastAPI, we can override dependencies specifically for testing. Here, we override the `get_db` dependency to yield our test session instead of the main production database session:

```python
@pytest.fixture
async def client(db_session: AsyncSession, mocked_aws):
    async def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac

    app.dependency_overrides.clear()
```

---

## 3. Writing and Running Tests

Tests are created using standard Python `assert` statements. For asynchronous routes, we use the `pytest.mark.anyio` (or `asyncio`) decorator and the `httpx.AsyncClient`.

**Example Test (`tests/test_posts.py`)**:
```python
@pytest.mark.anyio
async def test_get_posts_empty(client: AsyncClient):
    response = await client.get("/api/posts")
    
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 0
```

### Running the Suite

You can execute your entire test suite by running:
```powershell
python -m pytest -v
```

This will run all 12 tests across `test_posts.py` and `test_users.py` and output a full success report in just a few seconds!

---

*Notes by Balaji — covers all changes up to Part 17 (Automated Testing with Pytest).*
