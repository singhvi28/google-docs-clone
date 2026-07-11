# CollabDocs — Real-Time Collaborative Editor

A full-stack, real-time collaborative document editor inspired by Google Docs. Built with **React + Tiptap + Yjs** on the frontend and **FastAPI + PostgreSQL + Redis** on the backend, with optional **WebTransport (QUIC/HTTP3)** for ultra-low-latency editing alongside a classic **WebSocket** fallback.

![Dark Theme](https://img.shields.io/badge/theme-dark-1a1a2e?style=flat-square)
![Python](https://img.shields.io/badge/python-3.12-blue?style=flat-square&logo=python)
![TypeScript](https://img.shields.io/badge/typescript-6.0-blue?style=flat-square&logo=typescript)
![License](https://img.shields.io/badge/license-MIT-green?style=flat-square)

---

## ✨ Features

- **Real-Time Collaboration** — CRDT-based editing via Yjs with zero conflicts
- **Dual Transport** — Prefers WebTransport (QUIC/HTTP3, UDP :4433) for editors; falls back to WebSocket (TCP :8000) automatically
- **Live Cursors** — See other editors' cursors with names and colors in real-time (sent via QUIC datagrams when available)
- **Rich Text Editing** — Full formatting toolbar (headings, bold, italic, underline, lists, blockquotes, code, highlights, text alignment)
- **Google OAuth** — Secure authentication via Google sign-in
- **Editor Approval Workflow** — Document creators approve/deny edit access requests
- **50-Editor Limit** — Enforced concurrent editor cap with a waiting room UI
- **Read-Only Viewer** — Share a view-only link with SSE-based live updates
- **Dashboard** — 3-tab document management (Created / Edited / Viewed)
- **Auto-Persistence** — CRDT state cached in Redis, flushed to PostgreSQL on disconnect and periodically
- **Dark Mode** — Premium dark theme with glassmorphism, gradient accents, and micro-animations

---

## 🏗️ Architecture

```text
┌──────────────────────────────────────────────────────────────────────┐
│                            FRONTEND                                 │
│  React + TypeScript + Vite                                          │
│  ┌────────────┐  ┌────────────┐  ┌────────────────────────────────┐ │
│  │  Tiptap    │  │    Yjs     │  │     collabTransport.ts         │ │
│  │  Editor    │←→│   CRDT     │←→│  Prefer WebTransport (QUIC)    │ │
│  │            │  │            │  │  Fallback → WebSocket (TCP)    │ │
│  └────────────┘  └────────────┘  └───────────┬────────────────────┘ │
│                                              │                      │
│  ┌─────── Viewer (read-only) ────────────────┼────────────────────┐ │
│  │  EventSource ← SSE stream                │                    │ │
│  └───────────────────────────────────────────┼────────────────────┘ │
└──────────────────────────────────────────────┼──────────────────────┘
                                               │
              ┌────────────────────────────────┼──────────────────┐
              │  WebTransport (QUIC/HTTP3)      │  WebSocket (TCP) │
              │  UDP :4433 (preferred)          │  TCP :8000       │
              │  • streams → CRDT deltas       │  • JSON frames   │
              │  • datagrams → cursors          │                  │
              └────────────────────────────────┼──────────────────┘
                                               │
┌──────────────────────────────────────────────┼──────────────────────┐
│                         BACKEND              │                      │
│                                              │                      │
│  ┌───────────────────────────────────────────┼───────────────────┐  │
│  │  /ws/doc/{edit_key}   WebSocket (TCP)  ←──┤                   │  │
│  │  /wt/doc/{edit_key}   WebTransport     ←──┘                   │  │
│  │  Collaboration Handlers (shared logic)                        │  │
│  │  • Auth check → Permission check → Editor limit               │  │
│  │  • Broadcast updates to all editors                           │  │
│  │  • Approval workflow for new editors                          │  │
│  └───────────┬───────────────────────────────────────────────────┘  │
│              │                                                      │
│  ┌───────────┼──────────────────┐                                   │
│  │  /api/view/{view_key}/stream │  SSE (HTTP GET, text/event-stream)│
│  │  Read-only viewer handler    │  Unidirectional, no QUIC needed   │
│  └───────────┬──────────────────┘                                   │
│              │                                                      │
│  ┌───────────▼───────────┐    ┌──────────────────────────┐          │
│  │       Redis           │    │     PostgreSQL           │          │
│  │  • CRDT append-log    │───→│  • users                 │          │
│  │  • Pub/Sub channels   │    │  • documents (BYTEA)     │          │
│  │  • Editor counters    │    │  • document_permissions  │          │
│  │  • Approval queues    │    └──────────────────────────┘          │
│  └───────────────────────┘                                          │
│                                                                     │
│  FastAPI + Hypercorn (TCP :8000)   aioquic (UDP :4433, TLS required)│
└─────────────────────────────────────────────────────────────────────┘
```

### Detailed Component Breakdown

- **FastAPI + Hypercorn (ASGI, TCP :8000)**: Serves the REST API, WebSocket collaboration endpoint, and SSE viewer stream. Hypercorn's async event loop handles thousands of concurrent connections.
- **aioquic WebTransport Server (UDP :4433)**: A standalone QUIC/HTTP3 server (`app/webtransport_server.py`) that accepts WebTransport sessions for editors. Runs alongside the FastAPI process (both launched by `start.sh`). TLS is mandatory — the start script auto-generates a self-signed certificate if none exists.
- **Dual-Transport Negotiation**: The frontend's `collabTransport.ts` tries WebTransport first; if the browser lacks support or the QUIC handshake fails, it transparently falls back to a classic WebSocket connection. Both transports share the same Redis append-log and Pub/Sub, so editors on different transports see each other's changes.
- **QUIC Channel Multiplexing** (WebTransport only):
  - **Bidirectional streams** (reliable, ordered) — carry Yjs CRDT sync deltas and control messages (approve/deny editor)
  - **Datagrams** (unreliable, unordered) — carry awareness/cursor updates; fire-and-forget avoids head-of-line blocking that TCP/WebSocket would cause
- **Yjs (CRDT)**: Conflict-free Replicated Data Types handle all real-time editing complexity on the client side. The backend is entirely unaware of the document semantics; it blindly relays base64-encoded binary updates between clients.
- **SSE Viewer Stream**: Read-only viewers connect via `GET /api/view/{view_key}/stream`. The server pushes an initial CRDT snapshot followed by live Pub/Sub deltas as `text/event-stream` events. No QUIC needed — the stream is unidirectional.
- **Redis (State & Coordination)**:
  - **CRDT Append-Log**: An append-only list of binary updates per document, used to reconstruct the merged state for new joiners.
  - **Pub/Sub Channels**: Broadcasts updates across all backend processes/workers (horizontally scalable — no sticky sessions required).
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
│   │   ├── main.py                  # FastAPI app factory + lifespan
│   │   ├── config.py                # Pydantic settings (env vars)
│   │   ├── database.py              # Async SQLAlchemy engine + sessions
│   │   ├── models.py                # ORM: User, Document, DocumentPermission
│   │   ├── schemas.py               # Pydantic request/response models
│   │   ├── utils.py                 # Key generation, monikers, colors
│   │   ├── webtransport_server.py   # QUIC/WebTransport collab server (aioquic)
│   │   ├── routes/
│   │   │   ├── auth.py              # Google OAuth + JWT
│   │   │   ├── documents.py         # Document CRUD API
│   │   │   ├── collab.py            # WebSocket collaboration (TCP fallback)
│   │   │   └── viewer.py            # SSE read-only viewer
│   │   └── services/
│   │       └── redis_service.py     # Redis caching, pub/sub, counters
│   ├── tests/                       # Unit + integration tests
│   ├── start.sh                     # Launches both aioquic + Hypercorn
│   ├── Dockerfile
│   ├── requirements.txt
│   └── .env.example
├── frontend/
│   ├── src/
│   │   ├── main.tsx                 # App entry point
│   │   ├── App.tsx                  # Router + auth provider
│   │   ├── index.css                # Dark theme design system
│   │   ├── pages/
│   │   │   ├── Login.tsx            # Google OAuth login
│   │   │   ├── Dashboard.tsx        # Document management
│   │   │   ├── Editor.tsx           # Collaborative editor
│   │   │   ├── Viewer.tsx           # Read-only SSE viewer
│   │   │   └── AuthCallback.tsx     # OAuth redirect handler
│   │   ├── components/
│   │   │   ├── Toolbar.tsx          # Rich text formatting bar
│   │   │   └── ApprovalPopup.tsx    # Editor approval notification
│   │   ├── hooks/
│   │   │   └── useAuth.tsx          # Auth context + provider
│   │   └── lib/
│   │       ├── api.ts               # REST + WS + WT URL helpers
│   │       └── collabTransport.ts   # WebTransport/WebSocket negotiation
│   ├── index.html
│   ├── package.json
│   └── vite.config.ts
├── docker-compose.yml               # Postgres + Redis + Backend + Frontend
├── .env                             # Environment variables (git-ignored)
└── NOTES.md                         # Detailed architecture notes
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

# Option A: Full stack (Hypercorn + WebTransport QUIC server)
bash start.sh

# Option B: Hypercorn only (no QUIC, WebSocket-only editing)
hypercorn app.main:app --bind 0.0.0.0:8000 --reload
```

> **Note:** `start.sh` auto-generates a self-signed TLS certificate for the QUIC server
> if one doesn't exist at `certs/cert.pem`. Browsers require HTTPS for WebTransport.

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

### Backend

| Variable | Description | Default |
|---|---|---|
| `DATABASE_URL` | PostgreSQL async connection string | `postgresql+asyncpg://gdocs:gdocs_secret@localhost:5432/gdocs_prod` |
| `REDIS_URL` | Redis connection string | `redis://localhost:6379/0` |
| `GOOGLE_CLIENT_ID` | Google OAuth client ID | *(required)* |
| `GOOGLE_CLIENT_SECRET` | Google OAuth client secret | *(required)* |
| `JWT_SECRET` | Secret key for signing JWT tokens | *(required in prod)* |
| `FRONTEND_URL` | Frontend origin for CORS | `http://localhost:5173` |
| `BACKEND_URL` | Backend origin for OAuth callbacks | `http://localhost:8000` |
| `WEBTRANSPORT_PORT` | UDP port for QUIC/WebTransport server | `4433` |
| `TLS_CERTFILE` | TLS certificate path (required for QUIC) | `certs/cert.pem` |
| `TLS_KEYFILE` | TLS private key path (required for QUIC) | `certs/key.pem` |

### Frontend

| Variable | Description | Default |
|---|---|---|
| `VITE_API_URL` | Backend REST API base URL | `http://localhost:8000` |
| `VITE_WS_URL` | WebSocket base URL (TCP fallback) | `ws://localhost:8000` |
| `VITE_WT_URL` | WebTransport base URL (QUIC); empty to disable | *(empty — WebSocket only)* |

---

## 📡 API Endpoints

### REST API (HTTP, TCP :8000)

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

### Real-Time Endpoints

| Protocol | Path | Port | Description |
|---|---|---|---|
| **WebTransport** (QUIC) | `/wt/doc/{edit_key}` | UDP :4433 | Preferred editor transport — CRDT deltas on streams, cursors on datagrams |
| **WebSocket** (TCP) | `/ws/doc/{edit_key}` | TCP :8000 | Fallback editor transport — all messages as JSON frames |
| **SSE** (HTTP GET) | `/api/view/{view_key}/stream` | TCP :8000 | Read-only viewer — initial snapshot + live Pub/Sub deltas |

---

## 🛠️ Tech Stack

| Layer | Technology |
|---|---|
| **Frontend** | React 19, TypeScript, Vite, Tiptap (ProseMirror), Yjs, Lucide Icons |
| **Backend (TCP)** | Python 3.12, FastAPI, Hypercorn (ASGI), SQLAlchemy (async), Authlib |
| **Backend (QUIC)** | aioquic 1.2 — standalone HTTP/3 WebTransport server |
| **Database** | PostgreSQL 16 (documents stored as BYTEA CRDT blobs) |
| **Cache/Pub-Sub** | Redis 7 (CRDT append-log, editor counters, pub/sub, approval queues) |
| **Auth** | Google OAuth 2.0 + JWT |
| **Infra** | Docker Compose (TCP :8000, UDP :4433, Postgres, Redis) |

---

## 📄 License

MIT
