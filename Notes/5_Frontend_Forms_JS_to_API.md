# Frontend Forms - Connecting JavaScript to Your API (Create, Edit, Delete)

This document breaks down the flow of data between your HTML views, JavaScript logic, and FastAPI backend when performing CRUD (Create, Read, Update, Delete) operations.

## Architecture Overview
Instead of traditional HTML form submissions that trigger a full page reload, this app uses a "Single Page Application" style approach for forms. 
1. **Bootstrap Modals** are used to display forms over the current page.
2. **JavaScript** intercepts the form submissions.
3. **The Fetch API** sends the data to the FastAPI backend asynchronously in the background.

---

## 1. Create Post Flow
**Files involved:** `layout.html`, `routers/posts.py`

### Step-by-Step Flow:
1. **HTML (UI):** The "New Post" button in the Navbar (`layout.html`) triggers the `createPostModal` to appear. This modal contains a form (`#createPostForm`) with inputs for Title and Content.
2. **JavaScript (Action):** An event listener in the `<script>` tag at the bottom of `layout.html` listens for the `submit` event on this form. It immediately calls `event.preventDefault()` to stop the browser from refreshing the page.
3. **Background (Data Processing):**
   - JS collects the user's input using `new FormData(createForm)` and converts it into a plain JavaScript object: `{ title: "...", content: "..." }`.
   - It temporarily hardcodes `postData.user_id = 1` (this will be replaced by real user IDs once authentication is added).
   - JS uses the `fetch()` API to send a `POST` request to `/api/posts`. It converts the JS object into a JSON string (`JSON.stringify(postData)`).
4. **Backend (FastAPI):** The router in `posts.py` receives the JSON, validates it against the `PostCreate` Pydantic schema, saves it to the database, and returns the newly created post as JSON with a `201 Created` status code.
5. **Result (UI Update):** If successful, JS uses helper functions to hide the `createPostModal` and show the `successModal`. When the user closes the success modal, the page automatically reloads to display the new post.

---

## 2. Edit Post Flow
**Files involved:** `post.html`, `routers/posts.py`

### Step-by-Step Flow:
1. **HTML (UI):** On a specific post's page (`post.html`), clicking the "Edit Post" button opens the `editModal`. Jinja2 templating is used to pre-fill the inputs with the existing data (e.g., `value="{{ post.title }}"`).
2. **JavaScript (Action):** Similar to creation, the `editPostForm` has an event listener that prevents the default submission behavior.
3. **Background (Data Processing):**
   - JS extracts the updated form data.
   - It deletes `post_id` from the data object because the ID is passed in the URL path, not in the JSON body.
   - It sends a `PATCH` request to `/api/posts/${postId}` (using the specific post's ID).
4. **Backend (FastAPI):** The backend receives the `PATCH` request, locates the post by its ID in the database, updates only the provided fields, commits the changes, and returns the updated post.
5. **Result (UI Update):** JS hides the edit modal, displays the success modal, and reloads the page to reflect the newly edited content.

---

## 3. Delete Post Flow
**Files involved:** `post.html`, `routers/posts.py`

### Step-by-Step Flow:
1. **HTML (UI):** Clicking "Delete Post" in `post.html` opens the `deleteModal`. This isn't a form with inputs; it's just a warning confirmation box to prevent accidental deletions.
2. **JavaScript (Action):** An event listener is attached directly to the "Delete" button (`#confirmDelete`) inside the modal.
3. **Background (Data Processing):**
   - When clicked, JS sends a `DELETE` request to `/api/posts/${postId}` using the `fetch` API.
   - No body or JSON payload is required because the ID in the URL is all the backend needs to identify which record to delete.
4. **Backend (FastAPI):** The backend finds the post, deletes it from the database, and returns a `204 No Content` HTTP status code. This means the action succeeded, but there is no data to return.
5. **Result (UI Update):** Since the current post no longer exists, reloading the page would result in a 404 error. Instead, the JS intercepts the 204 success status and automatically redirects the user back to the home page (`window.location.href = "/";`).

---

## CSS & Utility Functions
- **Bootstrap (CSS/JS):** The project leverages Bootstrap for the Modals. This allows for forms that overlay the screen without needing separate `.html` pages for `/create` or `/edit`.
- **`utils.js` (JavaScript):** Functions like `showModal()`, `hideModal()`, and `getErrorMessage()` are extracted into a separate utility file. This prevents writing the exact same modal-toggling code in both `layout.html` and `post.html`.
