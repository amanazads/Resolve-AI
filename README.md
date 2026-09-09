# Resolve AI — Autonomous AI Execution Workspace

> **Resolve AI** is an open-source autonomous AI execution agent and workspace. It turns natural-language objectives into verified, audited, real-world actions without making the user configure complex workflow nodes, pipelines, or automation rules.

[![Tests](https://img.shields.io/badge/tests-42%20passed-brightgreen.svg)](backend/tests/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.109+-009688.svg?logo=fastapi)](https://fastapi.tiangolo.com)
[![React](https://img.shields.io/badge/React-18.3+-61DAFB.svg?logo=react)](https://react.dev)
[![Vite](https://img.shields.io/badge/Vite-5.4+-646CFF.svg?logo=vite)](https://vitejs.dev)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

---

## 1. What is Resolve AI?

Traditional AI applications fall into two extremes: passive chatbots that only converse, or brittle workflow builders requiring users to manually configure nodes, webhooks, and triggers.

**Resolve AI bridges this gap as an autonomous agent workspace:**
- **Natural Language First**: The user describes an outcome in plain English (e.g., *"Send an email to Ujjwal Sharma at sharmaujjwal2019@gmail.com asking if he would be interested in discussing pre-seed funding for CKRIPT."* or *"Analyze this CSV of leads and prepare outreach for relevant investors."*).
- **First-Class Task Lifecycle**: Objectives are encapsulated in a first-class `ExecutionTask` with distinct lifecycle states (`UNDERSTANDING`, `INSPECTING`, `WAITING_FOR_USER`, `PLANNING`, `WAITING_FOR_AUTHORIZATION`, `READY`, `RUNNING`, `VERIFYING`, `COMPLETED`, `FAILED`).
- **Deterministic Tool Runtime**: The LLM reasons, plans, and extracts intent, but **never** executes external actions directly. Actions are validated and executed by a deterministic Tool Registry.
- **Strict Server-Side Authorization**: Scoped permissions (`gmail.send`, etc.) are enforced on the backend with pre-send revocation checks.
- **Truthful Verification**: The system captures real provider receipts (e.g. Gmail `provider_message_id` and `thread_id`). A task is never marked `COMPLETED` unless the provider returns real success.
- **File Intelligence**: Supports multi-format context ingestion (PDF, DOCX, CSV, XLSX, TXT, JSON) with strict prompt injection isolation boundaries (`<UNTRUSTED_DOCUMENT_DATA>`).

---

## 2. Core Philosophy & Flow

```
USER OBJECTIVE 
      ↓ 
 UNDERSTAND 
      ↓ 
INSPECT CONTEXT & FILES 
      ↓ 
ASK QUESTIONS IF NECESSARY (WAITING_FOR_USER on same task_id)
      ↓ 
   PLAN 
      ↓ 
REQUEST REQUIRED AUTHORIZATION (WAITING_FOR_AUTHORIZATION)
      ↓ 
  EXECUTE (Deterministic Tool Registry & Idempotency)
      ↓ 
   VERIFY (Provider Confirmation & Message ID)
      ↓ 
REPORT RESULT (COMPLETED)
```

---

## 3. Architecture

```
┌────────────────────────────────────────────────────────────────────────┐
│                   React + Vite Agent Workspace                         │
│   • Composer (/new)            • Active Task Workspace (/tasks/:id)    │
│   • Task History (/tasks)      • Live Activity Timeline (/activity)    │
│   • Connections (/connections) • Operator Settings (/settings)        │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │ HTTP / REST / SSE
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│                        FastAPI REST API Layer                          │
│   • POST /api/tasks                    • GET /api/tasks                │
│   • GET /api/tasks/{task_id}           • POST /api/tasks/{id}/message  │
│   • POST /api/tasks/{id}/authorize     • POST /api/tasks/{id}/cancel   │
│   • POST /api/tasks/{id}/resume        • GET /api/tasks/{id}/events    │
│   • GET /api/integrations              • Gmail OAuth Routes            │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │
       ┌────────────────────────────┼────────────────────────────┐
       ▼                            ▼                            ▼
┌──────────────┐             ┌──────────────┐             ┌──────────────┐
│  Agent Loop  │             │   Security   │             │ File Parser  │
│  & Planner   │             │ & Identity   │             │ & Ingestion  │
│ (Gemini 2.5) │             │ (AuthUser)   │             │(PDF/CSV/DOCX)│
└──────┬───────┘             └──────┬───────┘             └──────┬───────┘
       │                            │                            │
       └────────────────────────────┼────────────────────────────┘
                                    │
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│                       Deterministic Tool Registry                      │
│   • send_email        • read_email        • search_contacts            │
│   • read_attachment   • web_search        • calculator                 │
│                                                                        │
│   Enforces: Server-Side Authorization │ Cryptographic Idempotency      │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │
          ┌─────────────────────────┴─────────────────────────┐
          ▼                                                   ▼
┌───────────────────┐                               ┌───────────────────┐
│ Outbound Provider │                               │ Persistent Store  │
│ • Real Gmail API  │                               │ • MongoDB Atlas   │
│ • Local Dry-Run   │                               │ • Audit Events    │
└───────────────────┘                               └───────────────────┘
```

---

## 4. Key Features

### 1. First-Class Task Abstraction
Tasks support $1, 2, 3, \dots, N$ dynamic actions. Every task is stored with:
- `id` / `task_id`, `user_id`, `objective`, `task_type`
- `status`: `UNDERSTANDING` → `INSPECTING` → `PLANNING` → `WAITING_FOR_AUTHORIZATION` → `RUNNING` → `VERIFYING` → `COMPLETED`
- `conversation_history`, `attachments`, `context`, `plan`, `actions`, `authorizations`, `execution_state`, `results`, `errors`, `events`

### 2. Multi-Turn Clarification on the Same Task
If an objective is ambiguous or lacks required parameters (e.g. missing recipient email or multiple contacts with the same name), Resolve AI enters `WAITING_FOR_USER`, generates clarifying questions, and resumes execution on the **exact same `task_id`** once answered.

### 3. File Intelligence & Injection Isolation
Supports uploading PDFs, Word documents (.docx), Excel spreadsheets (.xlsx), CSVs, JSON, and text notes up to 25MB. Files are verified via SHA-256 hashes and wrapped in `<UNTRUSTED_DOCUMENT_DATA>` boundaries, preventing documents from injecting prompt hijacking instructions.

### 4. Scoped Server-Side Authorization
Client-side `allow_send: true` is never trusted. The backend creates granular, task-scoped `PermissionGrant` records. Before any external provider executes, an atomic check verifies that authorization was explicitly granted and has not been revoked.

### 5. Truthful Gmail OAuth Integration
When sending via Gmail:
- Authenticates through Google Cloud OAuth 2.0.
- Uses the sender identity of the authenticated Google account (e.g., `Aman Azad <azadaman1apl@gmail.com>`).
- Encrypts refresh and access tokens at rest using 32-byte Fernet cryptography.
- Captures actual provider receipts (`provider_message_id`, `thread_id`).
- Zero silent fallbacks to mock sending when configured for Gmail.

### 6. Guaranteed Dry-Run Mode
When Dry Run is enabled (`dry_run: true`), Resolve AI runs the full pipeline—understanding intent, parsing files, formulating plans, generating copy, and validating parameters—while guaranteeing **zero external network transmissions**.

### 7. Modern Agent-First Frontend
A clean, minimal, distraction-free interface built with React and Vite:
- **Composer Mode** (`/new`): Minimal "What can I help you accomplish?" prompt bar with file attachment chips and quick prompt recommendations.
- **Active Task Workspace** (`/tasks/:id`): Visual lifecycle pipeline stage tracker, agent clarification dialogue, step-by-step plan, live action cards with email previews, scoped authorization bar, and real-time execution milestones.
- **Task History** (`/tasks`): Chronological log of past tasks grouped by date (`Today`, `Yesterday`, `Earlier`).
- **Connections** (`/connections`): Live status of integrations (Gmail, Web Search, Document Intelligence).
- **Settings** (`/settings`): Operator identity configuration and system health diagnostics.

---

## 5. Supported Tools in Registry

| Tool Name | Description | Required Permission | Supports Dry Run | Side Effects |
|---|---|---|---|---|
| `send_email` | Transmits an email via Gmail API or simulated transport | `gmail.send` | ✅ Yes | Yes |
| `read_email` | Reads threads or messages from connected mailbox | `gmail.readonly` | ✅ Yes | No |
| `search_contacts` | Searches normalized contacts in datasets or address book | `contacts.read` | ✅ Yes | No |
| `read_attachment` | Reads and extracts text/tables from uploaded task files | None | ✅ Yes | No |
| `web_search` | Performs external queries for research and verification | `search.read` | ✅ Yes | No |
| `calculator` | Evaluates deterministic mathematical expressions | None | ✅ Yes | No |

---

## 6. Local Development & Setup

### Prerequisites
- **Python**: 3.11+
- **Node.js**: 18+ and `npm`
- **MongoDB**: Local MongoDB instance (`mongodb://localhost:27017`) or MongoDB Atlas connection URI
- **Google Cloud Console Credentials** (for live Gmail integration)
- **Google Gemini API Key** (for autonomous planning)

---

### Step 1: Clone Repository & Configure Environment

```bash
git clone https://github.com/amanazads/Resolve-AI.git
cd Resolve-AI

# Copy environment template
cp .env.example .env
```

Edit `.env` and configure your keys:
- `GEMINI_API_KEY`: Your Gemini API key from Google AI Studio.
- `MONGODB_URI`: Your MongoDB connection string.
- `GMAIL_TOKEN_ENCRYPTION_KEY`: A 32-byte Fernet key. Generate with:
  ```bash
  python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
  ```
- `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET`: From Google Cloud Console.

---

### Step 2: Backend Setup & Launch

```bash
cd backend

# Create and activate virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Start backend server with hot-reload
python -m uvicorn app.main:app --reload --port 8000
```
Backend will be live at `http://localhost:8000` (API documentation at `http://localhost:8000/docs`).

---

### Step 3: Frontend Setup & Launch

Open a second terminal window:
```bash
cd frontend

# Install dependencies
npm install

# Start Vite development server
npm run dev
```
Frontend will be live at `http://localhost:3000`.

---

## 7. Running the Asynchronous Worker

For batch operations, background jobs, and worker crash recovery:
```bash
cd backend
source .venv/bin/activate
python -m app.workers.worker
```
The worker will atomically claim queued jobs from MongoDB, enforce idempotency keys (`task_id + action_id`), and report real-time status updates over SSE.

---

## 8. Running Automated Test Suites

Resolve AI includes comprehensive test suites covering unit tests, security audits, and full end-to-end user journeys:

```bash
cd backend
source .venv/bin/activate

# Run the complete autonomous workspace test suite (42 tests)
pytest tests/test_autonomous_workspace_scenarios.py tests/test_autonomous_workspace_audit.py tests/test_three_demos_end_to_end.py -v
```

### Test Coverage Highlights:
- **`test_autonomous_workspace_scenarios.py`** (23 tests):
  - Task lifecycle and state transitions
  - Multi-turn clarification with continuation on same `task_id`
  - Granular server-side authorization enforcement
  - Idempotency key deduplication
  - Dry run zero-side-effects guarantee
  - Provider failure propagation
  - File ingestion across PDF, DOCX, CSV, XLSX, TXT, JSON
- **`test_autonomous_workspace_audit.py`** (16 tests):
  - 16 production criteria: no fake provider IDs, truthful Gmail status, injection containment, user isolation.
- **`test_three_demos_end_to_end.py`** (3 tests):
  - Demo 1: Pre-seed outreach to Ujjwal Sharma via Gmail.
  - Demo 2: Spreadsheet lead filtering and draft generation.
  - Demo 3: Multi-document cross-file summarization without email permission request.

---

## 9. End-to-End Demo Walkthroughs

### Demo 1: Single-Action Pre-Seed Funding Outreach
1. Navigate to `http://localhost:3000/#/new`.
2. Enter the objective:
   > *"Send an email to Ujjwal Sharma at sharmaujjwal2019@gmail.com asking if he would be interested in discussing pre-seed funding for CKRIPT."*
3. Click **Run Objective**.
4. Resolve AI inspects the request, constructs a 4-step execution plan, and drafts professional outreach copy (`CKRIPT — Pre-Seed Funding Discussion`).
5. The task enters `WAITING_FOR_AUTHORIZATION`.
6. Click **Authorize & Run** in the authorization banner.
7. Resolve AI executes `send_email` via Gmail API, receives `provider_message_id`, transitions to `VERIFYING`, and completes with `COMPLETED`.

### Demo 2: Multi-Action Spreadsheet Lead Filtering
1. Navigate to `http://localhost:3000/#/new`.
2. Attach an investor contacts spreadsheet (`investors.csv` or `.xlsx`).
3. Enter:
   > *"Read the attached investor spreadsheet and identify the investors who are relevant for CKRIPT. Then prepare personalized outreach emails for them."*
4. Click **Run Objective**.
5. Resolve AI inspects the columns, identifies relevant investors, generates personalized drafts for each contact, and waits for user authorization before sending any emails.

### Demo 3: Read-Only Multi-Document Synthesis
1. Navigate to `http://localhost:3000/#/new`.
2. Attach three documents (e.g. `deck.pdf`, `notes.docx`, `market.txt`).
3. Enter:
   > *"Read these three documents and summarize the important information."*
4. Click **Run Objective**.
5. Resolve AI extracts document contents, synthesizes insights across all three files, and outputs the answer directly.
6. Notice that **no email authorization is requested**, proving the system is genuinely general-purpose and scoped to the task.

---

## 10. Security & Safety Model

- **Prompt Injection Isolation**: All file contents and external data are enclosed inside `<UNTRUSTED_DOCUMENT_DATA>` tags. The planner is strictly instructed to treat document content as data, ignoring embedded commands like `"Ignore previous instructions"`.
- **Zero Token Leakage**: OAuth access tokens and refresh tokens are encrypted using Fernet (AES-128-CBC + HMAC-SHA256) at rest. Tokens are never transmitted to the browser or returned in API responses.
- **Server-Side Authorization**: The frontend cannot bypass permissions. Every side-effecting action requires a valid `PermissionGrant` in MongoDB verified at the exact moment of execution.
- **Atomic Pre-Send Revocation**: If a user revokes authorization while a batch job is running, the next action checks the revocation state and aborts before calling the provider.
- **Cryptographic Idempotency**: Every external action has a unique idempotency key (`task_id + action_id`). Retrying or recovering from a crash will never result in duplicate emails.

---

## 11. Roadmap

- [x] Canonical `ExecutionTask` architecture with $N$ dynamic actions
- [x] Multi-format file intelligence (PDF, DOCX, CSV, XLSX, TXT, JSON)
- [x] Multi-turn clarification continuing on the same task
- [x] Dynamic user identity context (`AuthenticatedUser`)
- [x] Real Gmail OAuth 2.0 integration with provider receipts
- [x] Modern agent workspace UI (Claude/Linear aesthetic)
- [x] Resilient background worker with atomic leasing and crash recovery
- [ ] Multi-tenant organization workspaces
- [ ] Integration with Microsoft Outlook / Office 365
- [ ] Live web search research tool integration (SerpAPI / Tavily)
- [ ] Scheduled recurring tasks and proactive monitoring

---

## 12. License

Distributed under the MIT License. See `LICENSE` for details.
