# Backend Test Suite

This directory contains comprehensive unit and integration tests for the FastAPI backend, utilizing `pytest` and `pytest-asyncio`.

## Testing Architecture & Isolation

The production backend heavily relies on PostgreSQL and Redis, but the test suite is designed to be **completely service-free**. This allows tests to run instantly in local development and CI environments without requiring external dependencies (like Docker containers for databases).

- **Database Isolation**: Tests utilize a fresh, in-memory database (via SQLAlchemy's async SQLite dialect or mock session) per test. FastAPI's `get_db` dependency is overridden to inject this test session.
- **Redis Isolation**: Redis interactions are handled via a patched in-memory fake, bypassing the need for a real Redis server.
- **Auth Overrides**: Authenticated routes override the `get_current_user` dependency, allowing fixtures to inject mock users (creators, editors, viewers) without needing valid OAuth tokens.

## Directory Layout

### `unit/`
Unit tests isolate specific functions and helpers to ensure their logic is sound without booting the full FastAPI app.
- **`test_auth.py`**: Validates JWT encoding/decoding and token expiration logic.
- **`test_collab.py`**: Tests the WebSocket broadcast logic, ensuring messages are sent correctly, dead sockets are removed, and Redis state is correctly flushed to PostgreSQL.
- **`test_redis_service.py`**: Verifies the core logic of cache setting/getting, counter incrementing, and approval queue management (against the fake Redis implementation).
- **`test_utils.py`**: Checks moniker (name) generation and color string formatting.

### `integration/`
Integration tests boot an `AsyncClient` (ASGI test client) to simulate actual HTTP and WebSocket requests against the FastAPI app.
- **`test_auth_routes.py`**: Ensures the `/api/auth/me` profile endpoint returns the correct mock user profile.
- **`test_documents_routes.py`**: Tests the entire REST API lifecycle for documents. Includes creating documents, fetching document lists (with correct tab filtering for Created/Edited/Viewed), lookup by edit/view keys, and deletion permissions.
- **`test_health_and_viewer.py`**: Asserts the health check endpoint is responsive. Also verifies the SSE (Server-Sent Events) viewer stream, ensuring read-only clients receive the expected Yjs binary updates.

### `conftest.py`
The heart of the test suite. Provides shared `pytest` fixtures:
- `session_factory`: Provides the isolated in-memory database session.
- `make_user`, `make_document`, `make_permission`: Factory fixtures for quickly seeding the database.
- Dependency overrides applied automatically to the FastAPI app for `get_db` and Redis interactions.

## Running Tests

From the repository root:

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
pytest -v
```

If you already created the virtualenv from the repository root, this also works:

```bash
backend/.venv/bin/python -m pytest backend
```

## Coverage Goals
- Maintain zero-dependency tests to ensure fast CI runs.
- Prevent route-order regressions (especially for dynamic keys like `/api/documents/by-edit-key/{key}`).
- Ensure strict permission boundaries are respected (e.g., only creators can delete documents, unauthorized users are rejected).
