# CollabDocs — Real-Time Collaborative Editor

A full-stack, real-time collaborative document editor inspired by Google Docs. Built with **React + Tiptap + Yjs** on the frontend and **FastAPI + PostgreSQL + Redis** on the backend.

![Dark Theme](https://img.shields.io/badge/theme-dark-1a1a2e?style=flat-square)
![Python](https://img.shields.io/badge/python-3.12-blue?style=flat-square&logo=python)
![TypeScript](https://img.shields.io/badge/typescript-6.0-blue?style=flat-square&logo=typescript)
![License](https://img.shields.io/badge/license-MIT-green?style=flat-square)

---

## ✨ Features

- **Real-Time Collaboration** — CRDT-based editing via Yjs with zero conflicts
- **Live Cursors** — See other editors' cursors with names and colors in real-time
- **Rich Text Editing** — Full formatting toolbar (headings, bold, italic, underline, lists, blockquotes, code, highlights, text alignment)
- **Google OAuth** — Secure authentication via Google sign-in
- **Editor Approval Workflow** — Document creators approve/deny edit access requests
- **50-Editor Limit** — Enforced concurrent editor cap with a waiting room UI
- **Read-Only Viewer** — Share a view-only link with SSE-based live updates (3s interval)
- **Dashboard** — 3-tab document management (Created / Edited / Viewed)
- **Auto-Persistence** — CRDT state cached in Redis, flushed to PostgreSQL on disconnect and periodically
- **Dark Mode** — Premium dark theme with glassmorphism, gradient accents, and micro-animations

---

## 🏗️ Architecture

```text
┌─────────────────────────────────────────────────────────────┐
│                        FRONTEND                             │
│  React + TypeScript + Vite                                  │
│  ┌────────────┐  ┌────────────┐  ┌───────────────────────┐  │
│  │  Tiptap    │  │    Yjs     │  │  y-websocket provider │  │
│  │  Editor    │←→│   CRDT     │←→│  (WebSocket client)   │  │
│  └────────────┘  └────────────┘  └───────────┬───────────┘  │
└──────────────────────────────────────────────┼──────────────┘
                                               │ 
                                            WebSocket (binary
                                            CRDT updates)
┌──────────────────────────────────────────────┼──────────────┐
│                       BACKEND                │              │
│  FastAPI + Hypercorn (ASGI)                  │              │
│  ┌───────────────────────────────────────────┼───────────┐  │
│  │  /ws/doc/{edit_key}  <────────────────────┘           │  │
│  │  WebSocket Collaboration Handler                      │  │
│  │  • Auth check → Permission check → Editor limit       │  │
│  │  • Broadcast updates to all editors                   │  │
│  │  • Approval workflow for new editors                  │  │
│  └───────────┬───────────────────────────────────────────┘  │
│              │                                              │
│  ┌───────────▼───────────┐    ┌──────────────────────────┐  │
│  │       Redis           │    │     PostgreSQL           │  │
│  │  • CRDT state cache   │───→│  • users                 │  │
│  │  • Pub/Sub channels   │    │  • documents (BYTEA)     │  │
│  │  • Editor counters    │    │  • document_permissions  │  │
│  │  • Approval queues    │    └──────────────────────────┘  │
│  └───────────────────────┘                                  │
└─────────────────────────────────────────────────────────────┘
```

### Detailed Component Breakdown

- **FastAPI + Hypercorn (ASGI)**: Provides the asynchronous event loop necessary for handling thousands of concurrent WebSocket and SSE (Server-Sent Events) connections efficiently. It manages routing, dependency injection, and REST API endpoints.
- **Yjs (CRDT)**: Conflict-free Replicated Data Types handle all real-time editing complexity on the client side. The backend is entirely unaware of the document semantics; it blindly relays base64-encoded binary updates between clients.
- **WebSocket Collaboration Loop**:
  - Connections are held in process memory, grouped by the document's `edit_key`.
  - When a user sends an update, the backend immediately broadcasts it to other connected clients.
  - Periodic states are cached in Redis to instantly serve the latest state to new participants.
- **Redis (State & Coordination)**:
  - **CRDT Cache**: Caches the most recent document binary state for fast retrieval without querying PostgreSQL.
  - **Editor Counters**: Enforces the maximum concurrent editor limit (50) per document.
  - **Approval Queue**: Stores pending editors in temporary hash maps until the document creator approves or denies access.
- **PostgreSQL**: Serves as the ultimate source of truth. The document content is stored in a `BYTEA` column. When the last editor leaves the document, the backend flushes the cached state from Redis into PostgreSQL.

---

## 📁 Project Structure

```
google-docs-clone/
├── backend/
│   ├── app/
│   │   ├── __init__.py
│   │   ├── main.py              # FastAPI app factory + lifespan
│   │   ├── config.py            # Pydantic settings (env vars)
│   │   ├── database.py          # Async SQLAlchemy engine + sessions
│   │   ├── models.py            # ORM: User, Document, DocumentPermission
│   │   ├── schemas.py           # Pydantic request/response models
│   │   ├── utils.py             # Key generation, monikers, colors
│   │   ├── routes/
│   │   │   ├── auth.py          # Google OAuth + JWT
│   │   │   ├── documents.py     # Document CRUD API
│   │   │   ├── collab.py        # WebSocket collaboration
│   │   │   └── viewer.py        # SSE read-only viewer
│   │   └── services/
│   │       └── redis_service.py # Redis caching, pub/sub, counters
│   ├── tests/                   # Unit + integration tests
│   ├── Dockerfile
│   ├── requirements.txt
│   └── .env.example
├── frontend/
│   ├── src/
│   │   ├── main.tsx             # App entry point
│   │   ├── App.tsx              # Router + auth provider
│   │   ├── index.css            # Dark theme design system
│   │   ├── pages/
│   │   │   ├── Login.tsx        # Google OAuth login
│   │   │   ├── Dashboard.tsx    # Document management
│   │   │   ├── Editor.tsx       # Collaborative editor
│   │   │   ├── Viewer.tsx       # Read-only SSE viewer
│   │   │   └── AuthCallback.tsx # OAuth redirect handler
│   │   ├── components/
│   │   │   ├── Toolbar.tsx      # Rich text formatting bar
│   │   │   └── ApprovalPopup.tsx# Editor approval notification
│   │   ├── hooks/
│   │   │   └── useAuth.tsx      # Auth context + provider
│   │   └── lib/
│   │       └── api.ts           # REST + WS API client
│   ├── index.html
│   ├── package.json
│   └── vite.config.ts
├── docker-compose.yml           # Postgres + Redis + Backend + Frontend
├── .env                         # Environment variables (git-ignored)
└── CONTEXT.md                   # Original architecture spec
```

---

## 🚀 Quick Start

### Prerequisites

- **Node.js** ≥ 20
- **Python** ≥ 3.12
- **Docker** + **Docker Compose** (for Postgres & Redis)
- A **Google OAuth** client ID/secret ([console.cloud.google.com](https://console.cloud.google.com))

### 1. Clone & Configure

```bash
git clone <repo-url> google-docs-clone
cd google-docs-clone

# Copy and fill in your Google OAuth credentials
cp backend/.env.example .env
# Edit .env with your GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET
```

### 2. Start Infrastructure (Postgres + Redis)

```bash
docker compose up -d postgres redis
```

### 3. Backend Setup

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Run the server
hypercorn app.main:app --bind 0.0.0.0:8000 --reload
```

### 4. Frontend Setup

```bash
cd frontend
npm install
npm run dev
```

### 5. Open the App

Navigate to **http://localhost:5173** — you'll see the login page.

---

## 🧪 Running Tests

### Backend Tests

```bash
cd backend
pip install -r requirements-dev.txt   # pytest, httpx, etc.
pytest -v
```

Tests cover:
- **Unit**: auth utilities, Redis service, key generation, collaboration logic
- **Integration**: document CRUD routes, health endpoint, viewer SSE

---

## 🔧 Environment Variables

| Variable | Description | Default |
|---|---|---|
| `DATABASE_URL` | PostgreSQL async connection string | `postgresql+asyncpg://gdocs:gdocs_secret@localhost:5432/gdocs_prod` |
| `REDIS_URL` | Redis connection string | `redis://localhost:6379/0` |
| `GOOGLE_CLIENT_ID` | Google OAuth client ID | *(required)* |
| `GOOGLE_CLIENT_SECRET` | Google OAuth client secret | *(required)* |
| `JWT_SECRET` | Secret key for signing JWT tokens | *(required in prod)* |
| `FRONTEND_URL` | Frontend origin for CORS | `http://localhost:5173` |
| `BACKEND_URL` | Backend origin for OAuth callbacks | `http://localhost:8000` |

---

## 📡 API Endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/health` | Health check |
| `GET` | `/api/auth/login` | Initiate Google OAuth |
| `GET` | `/api/auth/callback` | OAuth callback (redirects to frontend) |
| `GET` | `/api/auth/me` | Get current user profile |
| `POST` | `/api/documents/` | Create new document |
| `GET` | `/api/documents/?tab=created` | List documents by tab |
| `GET` | `/api/documents/{id}` | Get document by ID |
| `DELETE` | `/api/documents/{id}` | Delete document (creator only) |
| `PATCH` | `/api/documents/{id}/title` | Rename document |
| `GET` | `/api/documents/by-edit-key/{key}` | Lookup by edit key |
| `GET` | `/api/documents/by-view-key/{key}` | Lookup by view key |
| `WS` | `/ws/doc/{edit_key}` | WebSocket collaboration |
| `GET` | `/api/view/{view_key}/stream` | SSE viewer stream |

---

## 🛠️ Tech Stack

| Layer | Technology |
|---|---|
| **Frontend** | React 19, TypeScript, Vite, Tiptap (ProseMirror), Yjs, y-websocket, Lucide Icons |
| **Backend** | Python 3.12, FastAPI, Hypercorn (ASGI), SQLAlchemy (async), Authlib |
| **Database** | PostgreSQL 16 (documents stored as BYTEA CRDT blobs) |
| **Cache/Pub-Sub** | Redis 7 (state caching, editor counters, pub/sub, approval queues) |
| **Auth** | Google OAuth 2.0 + JWT |
| **Infra** | Docker Compose |

---

## 📄 License

MIT
