# 8. Pagination — Hybrid SSR + Client-Side "Load More"

## Overview

Instead of loading all posts at once (which would kill performance as data grows), this app uses **offset-based pagination**. The architecture is a hybrid: the server renders the first page of posts (fast initial load, SEO-friendly), and JavaScript fetches subsequent pages asynchronously ("Load More" button, no full page reloads).

---

## 1. Configuration (`config.py`)

```python
class Settings(BaseSettings):
    posts_per_page: int = 10
```

The page size is centralised in `Settings`. Every paginated route reads `settings.posts_per_page` instead of a hardcoded `10`. Changing the value in `.env` updates all endpoints simultaneously.

---

## 2. Core Concept — Offset-Based Pagination

Offset pagination works by telling the database: *"skip the first N rows, then give me M rows."*

```
All posts (ordered by date DESC):
  [Post 1][Post 2][Post 3]...[Post 10] | [Post 11]...[Post 20] | [Post 21]...
   ← Page 1 (skip=0, limit=10) →        ← Page 2 (skip=10) →
```

**Two query parameters drive this:**
- `skip` (offset) — how many records to skip from the start
- `limit` — how many records to return

---

## 3. The `PaginatedPostsResponse` Schema (`schemas.py`)

```python
class PaginatedPostsResponse(BaseModel):
    posts: list[PostResponse]   # the actual data for this page
    total: int                  # total records in the DB (all pages)
    skip: int                   # the offset used for this request
    limit: int                  # the page size used
    has_more: bool              # True if more records exist beyond this page
```

`has_more` is the key field that drives the "Load More" button on the frontend. It tells the client: *"there is more data — feel free to request the next page."*

---

## 4. Backend API — `GET /api/posts` (`routers/posts.py`)

```python
@router.get("", response_model=PaginatedPostsResponse)
async def get_posts(
    db: Annotated[AsyncSession, Depends(get_db)],
    skip: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = settings.posts_per_page,
):
    # Step 1: Count total posts
    count_result = await db.execute(select(func.count()).select_from(models.Post))
    total = count_result.scalar() or 0

    # Step 2: Fetch the paginated slice
    result = await db.execute(
        select(models.Post)
        .options(selectinload(models.Post.author))
        .order_by(models.Post.date_posted.desc())
        .offset(skip)
        .limit(limit),
    )
    posts = result.scalars().all()

    # Step 3: Compute has_more
    has_more = skip + len(posts) < total

    return PaginatedPostsResponse(
        posts=[PostResponse.model_validate(post) for post in posts],
        total=total,
        skip=skip,
        limit=limit,
        has_more=has_more,
    )
```

### Query Parameter Validation with `Query()`

```python
skip:  Annotated[int, Query(ge=0)]           = 0
limit: Annotated[int, Query(ge=1, le=100)]   = settings.posts_per_page
```

- `ge=0` → `skip` must be ≥ 0 (no negative offsets)
- `ge=1` → `limit` must be ≥ 1 (must request at least one post)
- `le=100` → `limit` cannot exceed 100 (prevents clients from dumping the entire DB in one request)

FastAPI validates these automatically and returns a `422 Unprocessable Entity` if violated.

### Step 1 — Count Query (Two Queries, Not One)

```python
count_result = await db.execute(
    select(func.count()).select_from(models.Post)
)
total = count_result.scalar() or 0
```

We run a **separate `COUNT(*)` query** before the main data query. `total` is needed to compute `has_more` and let the frontend know how many posts exist overall.

> **Why not `len(all_posts)`?** That would fetch all records into memory just to count them — extremely inefficient. `SELECT COUNT(*)` is computed entirely in the database engine with no data transfer.

The `.scalar()` call extracts the single integer from the result. `or 0` handles the edge case where the table is empty (SQLAlchemy might return `None`).

### Step 2 — The Paginated Data Query

```python
select(models.Post)
    .options(selectinload(models.Post.author))  # eagerly load author
    .order_by(models.Post.date_posted.desc())   # newest first
    .offset(skip)                                # skip N rows
    .limit(limit)                               # return M rows
```

- `.options(selectinload(models.Post.author))` — each `Post` has a `author` relationship. Without eager loading, accessing `post.author` would fire a separate SQL query per post (the N+1 query problem). `selectinload` fetches all authors in a single extra query.
- `.order_by(models.Post.date_posted.desc())` — consistent ordering is **essential** for pagination. Without it, the DB can return rows in any order, and pages would overlap or miss records.
- `.offset(skip).limit(limit)` — this translates directly to `OFFSET skip LIMIT limit` in SQL.

### Step 3 — Computing `has_more`

```python
has_more = skip + len(posts) < total
```

Break this down:
- `skip` = records we already skipped (i.e., records shown in previous pages)
- `len(posts)` = records returned in this page
- `skip + len(posts)` = total records seen so far
- If that is less than `total`, there are still unseen records → `has_more = True`

**Example:**
```
total = 25, skip = 10, limit = 10 → we get 10 posts
skip + len(posts) = 10 + 10 = 20 < 25  → has_more = True  (5 more remain)

total = 25, skip = 20, limit = 10 → we get 5 posts
skip + len(posts) = 20 + 5 = 25 < 25  → has_more = False (nothing left)
```

---

## 5. User Posts Endpoint — `GET /api/users/{user_id}/posts` (`routers/users.py`)

The user-specific pagination follows the exact same pattern, with one extra step: verifying the user exists.

```python
@router.get("/{user_id}/posts", response_model=PaginatedPostsResponse)
async def get_user_posts(
    user_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    skip: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = settings.posts_per_page,
):
    # Verify user exists
    result = await db.execute(select(models.User).where(models.User.id == user_id))
    user = result.scalars().first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Count query (filtered by user)
    count_result = await db.execute(
        select(func.count())
        .select_from(models.Post)
        .where(models.Post.user_id == user_id),
    )
    total = count_result.scalar() or 0

    # Data query (filtered by user)
    result = await db.execute(
        select(models.Post)
        .options(selectinload(models.Post.author))
        .where(models.Post.user_id == user_id)
        .order_by(models.Post.date_posted.desc())
        .offset(skip)
        .limit(limit),
    )
    posts = result.scalars().all()

    has_more = skip + len(posts) < total

    return PaginatedPostsResponse(
        posts=[PostResponse.model_validate(post) for post in posts],
        total=total, skip=skip, limit=limit, has_more=has_more,
    )
```

The `WHERE models.Post.user_id == user_id` clause is added to both the count and data queries, so `total` and `has_more` reflect only *that user's* posts.

---

## 6. Server-Side Rendering — Initial Page Load (`main.py`)

The HTML routes render the **first page** of posts on the server. This gives:
- **Instant first paint** — the browser gets fully-rendered HTML, no JS needed to see content.
- **SEO** — search engines index the server-rendered HTML directly.

```python
@app.get("/", name="home")
async def home(request: Request, db: ...):
    count_result = await db.execute(select(func.count()).select_from(models.Post))
    total = count_result.scalar() or 0

    result = await db.execute(
        select(models.Post)
        .options(selectinload(models.Post.author))
        .order_by(models.Post.date_posted.desc())
        .limit(settings.posts_per_page),   # <-- no .offset() — always page 1
    )
    posts = result.scalars().all()

    has_more = len(posts) < total   # simpler: no skip, so len(posts) is all we've seen

    return templates.TemplateResponse(request, "home.html", {
        "posts": posts,
        "limit": settings.posts_per_page,   # passed to JS
        "has_more": has_more,               # passed to JS
    })
```

**Key difference from API:** There is no `.offset()` because this is always the first page. `has_more` is simpler: `len(posts) < total` (no `skip` to add).

Both `limit` and `has_more` are passed to the template — JavaScript uses them to initialize its pagination state.

The same pattern applies to `user_posts_page` in `main.py`, adding `.where(models.Post.user_id == user_id)` to both queries.

---

## 7. Jinja2 Template — Initial Render (`templates/home.html`)

```html
<div id="postsContainer">
    {% for post in posts %}
        <article class="content-section py-3 px-4 mb-4">
            ...
            <img src="{{ post.author.image_path }}" ...>
            <a href="{{ url_for('user_posts', user_id=post.author.id) }}">
                {{ post.author.username }}
            </a>
            <small>{{ post.date_posted.strftime("%B %d, %Y") }}</small>
            <h2><a href="{{ url_for('post_page', post_id=post.id) }}">{{ post.title }}</a></h2>
            <p>{{ post.content }}</p>
        </article>
    {% endfor %}
</div>

{% if has_more %}
    <div class="text-center mb-4">
        <button type="button" class="btn btn-outline-primary" id="loadMoreBtn">
            Load More Posts
        </button>
    </div>
{% endif %}
```

- `postsContainer` — the `div` with this ID is where JavaScript will append new posts.
- The "Load More" button is only rendered at all if `has_more` is `True`. If the first page contains all posts, no button appears and no JS runs.

---

## 8. Client-Side JavaScript — "Load More" (`home.html` inline script)

```javascript
import { escapeHtml, formatDate } from '/static/js/utils.js';

// Initialize state from server-rendered Jinja2 values
let currentOffset = {{ limit }};          // e.g., 10 (already showed the first 10)
const limit = {{ limit }};                // e.g., 10
let hasMore = {{ 'true' if has_more else 'false' }};

const postsContainer = document.getElementById('postsContainer');
const loadMoreBtn = document.getElementById('loadMoreBtn');
```

The server "seeds" the JS state via Jinja2 template interpolation. `currentOffset` starts at `limit` (e.g., 10) because the first 10 posts are already on the page — the next fetch should start from position 10.

### `createPostHTML(post)` — Matches Server Structure

```javascript
function createPostHTML(post) {
    return `
      <article class="content-section py-3 px-4 mb-4">
        <img src="${escapeHtml(post.author.image_path)}" ...>
        <a href="/users/${post.author.id}/posts">${escapeHtml(post.author.username)}</a>
        <small>${formatDate(post.date_posted)}</small>
        <h2><a href="/posts/${post.id}">${escapeHtml(post.title)}</a></h2>
        <p>${escapeHtml(post.content)}</p>
      </article>
    `;
}
```

This function replicates the exact same HTML structure as the Jinja2 template. A user scrolling through cannot tell which posts were server-rendered and which were client-rendered.

### `loadMorePosts()` — The Fetch Logic

```javascript
async function loadMorePosts() {
    loadMoreBtn.disabled = true;
    loadMoreBtn.textContent = 'Loading...';
    let errorOccurred = false;

    try {
        const response = await fetch(`/api/posts?skip=${currentOffset}&limit=${limit}`);
        if (!response.ok) throw new Error('Failed to fetch posts');

        const data = await response.json();

        for (const post of data.posts) {
            postsContainer.insertAdjacentHTML('beforeend', createPostHTML(post));
        }

        currentOffset += data.posts.length;   // advance the offset
        hasMore = data.has_more;

        if (!hasMore) {
            loadMoreBtn.classList.add('d-none');   // hide the button
        }
    } catch (error) {
        errorOccurred = true;
        loadMoreBtn.textContent = 'Error - Click to Retry';
        loadMoreBtn.disabled = false;
    } finally {
        if (!errorOccurred && hasMore) {
            loadMoreBtn.disabled = false;
            loadMoreBtn.textContent = 'Load More Posts';
        }
    }
}

if (loadMoreBtn) {
    loadMoreBtn.addEventListener('click', loadMorePosts);
}
```

**Step-by-step:**

| Action | Detail |
|--------|--------|
| **Disable button** | Prevents double-clicks during the fetch. Shows "Loading..." |
| **`fetch()`** | Hits the JSON API with the current `skip` and `limit`. |
| **`insertAdjacentHTML('beforeend', ...)`** | Appends HTML to the end of `postsContainer` without re-rendering existing posts. More efficient than `innerHTML +=`. |
| **`currentOffset += data.posts.length`** | Advances the offset by exactly how many posts were returned (may be less than `limit` on the last page). |
| **`hasMore = data.has_more`** | Uses the server's `has_more` flag (computed from the true `total`). |
| **Hide button** | If `has_more` is false, the button gets the `d-none` class (Bootstrap: `display:none`). |
| **Error handling** | On failure, the button re-enables with "Error - Click to Retry" so the user can try again. |
| **`finally` block** | Only re-enables the button if no error occurred AND there are more posts. |

---

## 9. XSS Prevention (`static/js/utils.js`)

```javascript
export function escapeHtml(text) {
    const div = document.createElement("div");
    div.textContent = text;
    return div.innerHTML;
}
```

When injecting user-generated content into the DOM via template literals (as in `createPostHTML`), you **must** escape it. If a malicious user creates a post with `title = "<script>steal_cookies()</script>"`, `escapeHtml` converts it to `&lt;script&gt;...&lt;/script&gt;` — harmless text.

**How it works:** Setting `div.textContent` lets the browser safely parse the string as plain text. Reading back `div.innerHTML` returns the HTML-entity-encoded version.

```javascript
export function formatDate(dateString) {
    const date = new Date(dateString);
    return date.toLocaleDateString("en-US", {
        year: "numeric",
        month: "long",
        day: "2-digit",
    });
}
```

The API returns ISO 8601 timestamps (e.g., `"2024-01-15T10:30:00Z"`). The server renders these using Python's `strftime("%B %d, %Y")` → `"January 15, 2024"`. `formatDate()` replicates that exact format using the browser's locale API, so client-rendered dates look identical to server-rendered ones.

---

## 10. Summary — The Full Pagination Flow

```
Initial Page Load (Browser requests GET /)
    │
    ├─ Server: COUNT(*) → total = 25
    ├─ Server: SELECT * ... LIMIT 10 → posts = [1..10]
    ├─ Server: has_more = (10 < 25) = True
    └─ Server: Renders home.html with posts + limit=10 + has_more=true
               JS initializes: currentOffset=10, hasMore=true
               "Load More" button is visible

User clicks "Load More"
    │
    ├─ JS: fetch("/api/posts?skip=10&limit=10")
    ├─ API: COUNT(*) → total=25, SELECT ... OFFSET 10 LIMIT 10 → [11..20]
    ├─ API: has_more = (10 + 10 < 25) = True
    ├─ JS: Appends posts 11-20 to postsContainer
    └─ JS: currentOffset=20, hasMore=true, button stays visible

User clicks "Load More" again
    │
    ├─ JS: fetch("/api/posts?skip=20&limit=10")
    ├─ API: SELECT ... OFFSET 20 LIMIT 10 → [21..25] (only 5 posts)
    ├─ API: has_more = (20 + 5 < 25) = False
    ├─ JS: Appends posts 21-25 to postsContainer
    └─ JS: currentOffset=25, hasMore=false → button hidden
```

## Why This Hybrid Approach?

| Concern | SSR (Initial) | CSR (Load More) |
|---------|--------------|-----------------|
| **SEO** | ✅ Full HTML indexed | ❌ JS-only content not indexed |
| **First Paint Speed** | ✅ Instant — no JS wait | N/A |
| **Subsequent Pages** | ❌ Full page reload | ✅ Instant, no reload |
| **Server Load** | Higher (renders HTML) | Lower (JSON only) |

This is why the initial load uses SSR and subsequent pages use the JSON API — you get the best of both worlds.
