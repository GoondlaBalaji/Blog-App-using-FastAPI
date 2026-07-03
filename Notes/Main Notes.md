# Main Notes

To understand exactly how your application works, let's look at the big picture. Your application has **two distinct layers** that share the same database:

1. **The Web UI Layer:** Renders HTML templates for normal web browsers.
2. **The Backend API Layer:** Processes raw JSON data for frontend applications or mobile clients and populates your Swagger UI documentation (`/docs`).

Here is a clear, endpoint-by-endpoint breakdown of what is happening under the hood, why each route is needed, and how they link back to your database relationships.

-----------------------------------------------------------------------------------------------------------------------------------------------------

## 1. Web UI Routes (HTML Template Output)

### `GET /` and `GET /posts`

* **The Main Work:** Fetches every single post record from the database and passes them to your `home.html` template.
* **Why it’s needed:** It serves as the main homepage of your website where users can scroll through a timeline of all blog entries.
* **The Database Link:** It executes a `select(models.Post)` query to retrieve all data rows currently sitting inside your SQLite `posts` table.

### `GET /posts/{post_id}`

* **The Main Work:** Uses the **Path Parameter** `{post_id}` to locate a single specific blog entry and passes it to the `post.html` template. It also limits the title text to the first 50 characters for clean tab layout.
* **Why it’s needed:** When a user clicks a post on your homepage, this route opens up that specific post's dedicated reading page. If they type an invalid ID (like `/posts/999`), it triggers a `404 Not Found` exception.
* **The Database Link:** It links directly to `models.Post.id`. Because of the `relationship()` configuration inside your database model, the HTML template can cleanly output `post.author.username` to show who wrote it, even though the query only targetted the post itself.

### `GET /users/{user_id}/posts`

* **The Main Work:** First confirms if the target user exists. If they do, it fetches *only* the posts written by that specific user and displays them on a dedicated profile-style page (`user_posts.html`).
* **Why it’s needed:** It gives users a customized feed to read all entries published by their favorite blogger.
* **The Database Link:** This is where your **One-to-Many relationship** shines. It cross-references `models.Post.user_id` with the incoming `user_id` path parameter, linking your two tables together.

-----------------------------------------------------------------------------------------------------------------------------------------------------

## 2. Backend API Routes (JSON Output & Data Processing)

### `POST /api/users`

* **The Main Work:** Checks if an incoming username or email is already taken. If they are unique, it registers a new account by saving a record into the `users` table with a `201 Created` status code.
* **Why it’s needed:** It serves as your registration gatekeeper. No one can write a post until they have created an account using this endpoint.
* **The Schema Link:** It uses `UserCreate` to enforce character rules on entry and uses `UserResponse` on exit to guarantee safe, structured output format.

### `GET /api/users/{user_id}`

* **The Main Work:** Looks up a user account by their ID and returns their profile details as flat JSON data.
* **Why it’s needed:** Essential for frontend apps to fetch profile information (like displaying an account avatar icon).
* **The Database Link:** Queries `models.User.id` directly from your SQL engine.

### `GET /api/posts`

* **The Main Work:** Queries the database and extracts all rows inside the `posts` table, outputting them as a clean JSON list.
* **Why it’s needed:** It acts as a raw data pipeline. If you or another developer want to build a mobile app or a React frontend later, they fetch data directly from this URL.
* **The Schema Link:** It enforces `response_model=list[PostResponse]`, ensuring no internal database columns leak to the client side.

### `GET /api/posts/{post_id}`

* **The Main Work:** Finds a specific post by its ID and outputs its JSON attributes.
* **Why it’s needed:** Fetches raw data for a single post card or detailed view overlay.
* **The Database Link:** Searches `models.Post.id`.

### `GET /api/users/{user_id}/posts`

* **The Main Work:** Validates that a user profile exists, then dumps all JSON post payloads created by that specific author.
* **Why it’s needed:** Provides programmatic data feeds for single-user portfolios.
* **The Database Link:** Filters rows matching `models.Post.user_id == user_id`.

### `POST /api/posts`

* **The Main Work:** Verifies if the author (`user_id`) exists in your system. If they do, it accepts your clean `PostCreate` schema inputs and inserts a brand new blog post row into the database.
* **Why it’s needed:** It is the primary data submission mechanism for publishing content.
* **The Critical Link:** It ties the input schema directly to the database layer. By verifying that the `post.user_id` actually points to a real entry in your `users` table, it protects your database configuration from becoming corrupted with orphaned, authorless posts.

-----------------------------------------------------------------------------------------------------------------------------------------------------

## Architectural Mapping of Links

To see exactly how these moving parts relate to each other, look at how an execution flows across these domains:

```text
 User Interface Layer (HTML)        Database Tables (SQL)         Data Delivery Layer (API)
 
 ┌────────────────────────┐      ┌────────────────────────┐      ┌────────────────────────┐
 │   home.html template   │◄────►│      posts table       │◄────►│    /api/posts (JSON)   │
 └────────────────────────┘      └───────────▲────────────┘      └────────────────────────┘
                                             │ (Linked via Foreign Key)
 ┌────────────────────────┐      ┌───────────┴────────────┐      ┌────────────────────────┐
 │ user_posts.html page   │◄────►│      users table       │◄────►│  /api/users (JSON)     │
 └────────────────────────┘      ┌────────────────────────┘      └────────────────────────┘

```

Your system is designed as a hybrid platform. No matter whether your users visit the website manually through the HTML template routes or access data programmatically down your secure API channels, they are pulling facts from the exact same single source of truth: your SQLite `blog.db` engine file.