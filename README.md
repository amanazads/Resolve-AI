# Resolve AI — Autonomous Outreach Automation Agent

Resolve AI is a **production-grade autonomous AI automation platform** built on FastAPI, LangGraph, and React/Vite.
It lets you upload a spreadsheet of contacts, describe your goal in plain English, and watch the system plan, personalise, and execute a compliant outreach campaign — all without writing a single line of code.

---

## Table of Contents

1. [Product Principle](#product-principle)
2. [Architecture](#architecture)
3. [Key Features](#key-features)
4. [Tech Stack](#tech-stack)
5. [Project Structure](#project-structure)
6. [Contact & Dataset Intelligence](#contact--dataset-intelligence)
7. [Campaign Lifecycle](#campaign-lifecycle)
8. [Personalization Engine](#personalization-engine)
9. [Safety & Compliance](#safety--compliance)
10. [Real-Time Monitoring](#real-time-monitoring)
11. [OAuth & Integration Setup](#oauth--integration-setup)
12. [Environment Variables](#environment-variables)
13. [Local Development Setup](#local-development-setup)
14. [Production Deployment](#production-deployment)
15. [API Reference](#api-reference)
16. [Running Tests](#running-tests)

---

## Product Principle

> **The LLM plans. Deterministic tools execute. MongoDB persists state. The system is resumable and idempotent.**

| Layer | Role |
|---|---|
| **LLM (Gemini)** | Reads intent, plans campaigns, generates personalised copy |
| **Deterministic tools** | Send emails, store data, update job status |
| **MongoDB** | Persists every job, event, and idempotency key |
| **Worker** | Claims jobs atomically; never double-sends |

The LLM never has direct access to external APIs. Every outbound action goes through an audited, rate-limited tool layer.

---

## Architecture

```
User / Browser  ←→  React + Vite Frontend
                         ↕
                    FastAPI REST API  ←(SSE/WS)→  Campaign Event Bus
                         ↕
          ┌──────────────┼──────────────┐
     Campaign Service  Contacts API  Safety API
          ↓
     LLM Planner (Gemini)
          ↓
     Job Queue (MongoDB)  ←─── Campaign Worker
                                    ↓
                          Personalization Engine
                                    ↓
                          Suppression Check
                                    ↓
                          Gmail / SMTP Adapter
```

---

## Key Features

### Autonomous Campaign Planning
Describe your goal in plain English. The Gemini planner decomposes it into a structured campaign plan with audience targeting, channel selection, personalisation strategy, and per-contact job generation.

### Contact & Dataset Intelligence
Upload CSV or XLSX files with *any* column names. The system:
- Automatically maps columns to a normalised contact schema
- Validates and deduplicates email addresses
- Classifies contacts as INVESTOR, VC, FOUNDER, HR, RECRUITER, or OTHER
- Supports up to 10,000 contacts per dataset

### AI-Powered Personalisation
For every recipient the engine generates a unique email using:
- Recipient profile (name, firm, role, investment focus)
- Sender / startup profile
- Campaign objective and tone
- Structured validation — no hallucinated facts

### Safety & Compliance
- Pre-flight validation: duplicate emails, malformed addresses, missing sender identity
- Suppression list: permanent opt-out, bounce, and unsubscribe tracking
- Rate limiting: per-provider, per-campaign send limits
- Dry-run mode: mandatory first-run; shows all generated emails before anything is sent
- Audit log: every decision recorded with timestamps, reasons, and user IDs

### Real-Time Monitoring
Live campaign progress via Server-Sent Events (SSE) or WebSocket:
- Jobs processed / sent / failed / retried
- Campaign lifecycle events (STARTED → PAUSED → COMPLETED)
- Per-recipient activity timeline
- Worker health status

### Resumable & Idempotent Execution
- Every campaign job has a cryptographic idempotency_key
- $setOnInsert + unique index prevents double-insertion
- Atomic find_one_and_update claim prevents double-execution
- Stale lease recovery automatically re-queues crashed-worker jobs
- Campaigns survive process restarts from the exact job they stopped at

### Legacy Chat Agent (retained)
Original LangGraph customer-support chatbot with intent routing, ChromaDB RAG, mock business tools, and human escalation is fully preserved and accessible via /api/chat.

---

## Tech Stack

### Backend
| Package | Purpose |
|---|---|
| FastAPI | Async REST API framework |
| LangGraph | State-graph orchestrator for the chat agent |
| Google Gemini 2.5 Flash | Campaign planner + personalization LLM |
| Motor (AsyncIO) | Async MongoDB driver |
| MongoDB | Persistent job queue, idempotency, audit, suppression |
| ChromaDB | Vector store for RAG knowledge base |
| Pydantic v2 | Strict request / response validation |
| httpx | Async HTTP client for OAuth flows |
| cryptography | Fernet encryption for stored OAuth tokens |
| openpyxl / pandas | XLSX and CSV parsing |

### Frontend
| Package | Purpose |
|---|---|
| React 18 + Vite | SPA framework |
| Vanilla CSS | Custom glassmorphism dark-mode design system |
| Lucide React | Icon set |
| Axios | API client |
| EventSource API | Native SSE for live campaign monitoring |

---

## Project Structure

```
.
├── backend/
│   └── app/
│       ├── automation/             # Core worker execution engine
│       │   ├── models.py           # AutomationRun, CampaignJob schemas
│       │   ├── planner.py          # LLM plan → job list
│       │   ├── executor.py         # Per-job send logic
│       │   ├── worker.py           # Worker loop, lease management, pause/resume/cancel
│       │   ├── queue.py            # CampaignJobQueue with atomic claim
│       │   ├── rate_limiter.py     # Per-provider token-bucket rate limiter
│       │   ├── retry_service.py    # Exponential backoff, error classification
│       │   └── campaign_service.py # High-level campaign orchestrator
│       ├── campaigns/              # Campaign planning & routes
│       │   ├── models.py
│       │   ├── planner.py          # Gemini-backed campaign plan generation
│       │   ├── service.py          # Campaign CRUD + state transitions
│       │   ├── routes.py           # REST endpoints + SSE streaming
│       │   ├── events.py           # CampaignEventBus (SSE + WebSocket + MongoDB)
│       │   └── prompts.py
│       ├── contacts/               # Dataset & contact intelligence
│       │   ├── parser.py           # CSV / XLSX parser
│       │   ├── schema.py           # Normalised Contact schema
│       │   ├── normalizer.py       # Column mapping & name splitting
│       │   ├── classifier.py       # Contact type inference
│       │   └── validator.py        # Email validation & dedup
│       ├── personalization/        # AI email generation
│       │   ├── generator.py        # Strategy dispatcher
│       │   ├── prompts.py          # Per-strategy LLM prompts
│       │   ├── validator.py        # Hallucination & length checks
│       │   └── templates.py        # Fallback template engine
│       ├── safety/                 # Compliance & rate-limit enforcement
│       │   ├── policy.py
│       │   ├── validator.py        # Pre-flight campaign validator
│       │   ├── suppression.py      # Opt-out / bounce suppression service
│       │   ├── rate_limits.py
│       │   └── audit.py
│       ├── integrations/           # OAuth provider adapters
│       │   ├── gmail.py
│       │   └── routes.py
│       ├── permissions/            # Authorization grants
│       ├── agents/                 # Legacy LangGraph chat agent
│       ├── rag/                    # ChromaDB RAG
│       ├── llm/                    # Gemini client & prompts
│       ├── tools/                  # Mock business tools
│       ├── database/
│       │   └── mongodb.py          # Async MongoDB manager + index management
│       ├── config.py
│       └── main.py
├── frontend/
│   └── src/
│       ├── components/
│       ├── hooks/                  # useCampaignEvents (SSE)
│       └── services/
├── backend/tests/
│   ├── test_contacts.py
│   ├── test_campaigns.py
│   ├── test_personalization.py
│   ├── test_safety.py
│   ├── test_automation.py
│   └── test_campaign_monitoring.py
├── knowledge_base/
├── scripts/
│   ├── ingest.py
│   └── evaluate.py
├── .env.example
├── docker-compose.yml
└── requirements.txt
```

---

## Contact & Dataset Intelligence

### Supported File Formats
- CSV (any encoding, comma or semicolon delimited)
- XLSX (multi-sheet; first sheet used by default)

### Automatic Column Mapping

| Normalised Field | Accepted Column Names |
|---|---|
| first_name | First Name, Given Name, fname |
| last_name | Last Name, Surname, Family Name |
| full_name | Name, Full Name, Contact Name |
| email | Email, Email Address, E-mail |
| company | Company, Firm, Organization, Employer |
| role | Role, Title, Designation, Position, Job Title |
| linkedin_url | LinkedIn, LinkedIn URL, Profile URL |
| investment_focus | Investment Focus, Focus Areas, Thesis |
| location | Location, City, Country |

### Contact Type Classification

| Type | Signals |
|---|---|
| INVESTOR | angel, investor, partner at fund |
| VC | venture, vc, capital, fund, managing director |
| FOUNDER | founder, co-founder, ceo, cto (without fund context) |
| HR | hr, human resources, people, talent |
| RECRUITER | recruiter, talent acquisition, staffing |
| OTHER / UNKNOWN | everything else |

### Upload API
```
POST /api/contacts/upload
Content-Type: multipart/form-data
Body: file=<csv or xlsx>

Response: { dataset_id, total_rows, valid_contacts, invalid_contacts, type_distribution }
```

---

## Campaign Lifecycle

```
DRAFT → PLAN_GENERATED → DRY_RUN → AUTHORIZED → SENDING → PAUSED / CANCELLED / COMPLETED
```

### 1. Create a Campaign
```json
POST /api/campaigns/
{
  "name": "Seed Round Outreach",
  "objective": "Send personalised fundraising emails to investors requesting a 20-minute call.",
  "dataset_id": "<dataset_id>",
  "communication_channel": "EMAIL"
}
```

### 2. Generate the Plan
```
POST /api/campaigns/{campaign_id}/plan
```

### 3. Dry Run (mandatory on first campaign)
```
POST /api/campaigns/{campaign_id}/dry-run
```

### 4. Authorize
```json
POST /api/campaigns/{campaign_id}/authorize
{ "user_id": "...", "confirmed": true }
```

### 5. Start
```
POST /api/campaigns/{campaign_id}/start
```

### 6. Pause / Resume / Cancel
```
POST /api/campaigns/{campaign_id}/pause
POST /api/campaigns/{campaign_id}/resume
POST /api/campaigns/{campaign_id}/cancel
```

---

## Personalization Engine

Three built-in strategies selected automatically by campaign type:

### INVESTOR_OUTREACH
Uses: first name · firm · investment focus · startup pitch · ask for call.
Never invents portfolio companies or fund sizes.

### JOB_OUTREACH
Uses: name · company · their product/domain · relevant experience match.

### INTERNSHIP_OUTREACH
Uses: name · company · role · relevant technical background.

### Structured Output
```python
PersonalizedMessage(
    recipient_email: str,
    recipient_name: str,
    subject: str,
    body: str,
    personalization_used: list[str],
    confidence: float,             # 0.0–1.0
    validation_status: str,        # VALID | WARNING | REJECTED
    warnings: list[str]
)
```

---

## Safety & Compliance

### Pre-Flight Campaign Validation
- At least one valid recipient
- No duplicate email addresses in the batch
- No malformed email addresses
- Sender identity configured
- Integration connected and not revoked
- Dry run completed (first campaign)
- No suppressed recipients

### Suppression List
Any contact that has previously opted out, bounced permanently (5xx), or requested no further contact is recorded and can never be messaged again.

### Rate Limits (defaults, configurable)

| Provider | Per-day | Per-hour | Per-minute |
|---|---|---|---|
| Gmail | 500 | 100 | 10 |
| SMTP | 1000 | 200 | 20 |

---

## Real-Time Monitoring

### Event Types
| Event | Triggered When |
|---|---|
| CAMPAIGN_CREATED | Campaign document first saved |
| PLAN_GENERATED | LLM planner returns a plan |
| DRY_RUN_COMPLETED | Dry-run finishes |
| CAMPAIGN_STARTED | Worker begins processing jobs |
| MESSAGE_GENERATED | Personalised copy produced |
| MESSAGE_SENT | SMTP / API confirms delivery |
| MESSAGE_FAILED | Transient or permanent send failure |
| MESSAGE_RETRIED | Job re-queued after backoff |
| CAMPAIGN_PAUSED | Pause requested |
| CAMPAIGN_RESUMED | Resume requested |
| CAMPAIGN_CANCELLED | Cancel requested |
| CAMPAIGN_COMPLETED | All jobs terminal |

### SSE Subscription
```javascript
const source = new EventSource(`/api/campaigns/${id}/events`);
source.onmessage = (e) => {
  const event = JSON.parse(e.data);
  // { event_type, campaign_id, data, timestamp }
};
```

---

## OAuth & Integration Setup

### Gmail OAuth2

1. Create a Google Cloud project and enable the Gmail API.
2. Create an OAuth 2.0 Client ID (Web Application type).
3. Add `http://localhost:8000/api/integrations/gmail/callback` as Authorised Redirect URI.
4. Set in `.env`:

```ini
GOOGLE_CLIENT_ID=...
GOOGLE_CLIENT_SECRET=...
GOOGLE_REDIRECT_URI=http://localhost:8000/api/integrations/gmail/callback
```

5. Go to **Settings → Integrations → Connect Gmail** in the UI.

OAuth tokens are encrypted with Fernet (AES-128-CBC + HMAC-SHA256) before being written to MongoDB. The encryption key is `INTEGRATION_ENCRYPTION_KEY` in `.env`. Plaintext tokens are never logged.

### Revoking Access
```
DELETE /api/integrations/gmail/{account_id}
```
Deletes the integration record. The campaign worker checks integration status before every send; a revoked integration causes an immediate campaign pause.

---

## Environment Variables

Copy `.env.example` to `.env` and fill in all required values.

```ini
# Core
GEMINI_API_KEY=your_gemini_api_key_here
LLM_MODEL=gemini-2.5-flash

# MongoDB
MONGODB_URI=mongodb://localhost:27017
MONGODB_DB_NAME=resolve_ai_db

# ChromaDB
CHROMA_PERSIST_DIRECTORY=./chroma_db

# Google OAuth (Gmail integration)
GOOGLE_CLIENT_ID=
GOOGLE_CLIENT_SECRET=
GOOGLE_REDIRECT_URI=http://localhost:8000/api/integrations/gmail/callback

# Integration security
# Generate: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
INTEGRATION_ENCRYPTION_KEY=

# Campaign rate limits (optional overrides)
GMAIL_DAILY_LIMIT=500
GMAIL_HOURLY_LIMIT=100
GMAIL_MINUTE_LIMIT=10

# Worker
WORKER_LEASE_SECONDS=30
WORKER_POLL_INTERVAL=2
CAMPAIGN_MAX_RETRIES=3
```

---

## Local Development Setup

### Prerequisites
- Python 3.11+
- Node.js 18+
- MongoDB 6+ (local or Atlas)
- Google Cloud project with Gmail API enabled

### 1. Clone & configure
```bash
git clone https://github.com/your-org/resolve-ai.git
cd resolve-ai
cp .env.example .env
# Edit .env with your keys
```

### 2. Backend
```bash
python3 -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt

# Ingest knowledge base into ChromaDB
python scripts/ingest.py

# Start API server
python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Swagger UI: http://localhost:8000/docs

### 3. Frontend
```bash
cd frontend
npm install
npm run dev
```

UI: http://localhost:3000

---

## Production Deployment

### Docker Compose (all-in-one)
```bash
docker-compose up --build
```

| Service | Port |
|---|---|
| Frontend | 3000 |
| FastAPI Backend | 8000 |
| MongoDB | 27017 |

### Production Checklist
- [ ] Use MongoDB Atlas (or a replica set)
- [ ] Set a strong INTEGRATION_ENCRYPTION_KEY and store in a secrets manager
- [ ] Use HTTPS — terminate TLS at nginx / load balancer
- [ ] Set GOOGLE_REDIRECT_URI to your production domain
- [ ] Enable MongoDB Atlas IP allowlisting
- [ ] Configure nginx rate-limiting on the upload endpoint
- [ ] Set WORKER_LEASE_SECONDS >= 60 in production
- [ ] Never log GEMINI_API_KEY, GOOGLE_CLIENT_SECRET, or INTEGRATION_ENCRYPTION_KEY

---

## API Reference

### Campaigns
| Method | Path | Description |
|---|---|---|
| POST | /api/campaigns/ | Create campaign |
| GET | /api/campaigns/ | List all campaigns |
| GET | /api/campaigns/{id} | Get campaign details |
| POST | /api/campaigns/{id}/plan | Generate LLM plan |
| POST | /api/campaigns/{id}/dry-run | Preview generated emails |
| POST | /api/campaigns/{id}/authorize | Grant execution permission |
| POST | /api/campaigns/{id}/start | Begin execution |
| POST | /api/campaigns/{id}/pause | Pause campaign |
| POST | /api/campaigns/{id}/resume | Resume paused campaign |
| POST | /api/campaigns/{id}/cancel | Cancel campaign |
| GET | /api/campaigns/{id}/jobs | List campaign jobs |
| GET | /api/campaigns/{id}/activity | Activity event log |
| GET | /api/campaigns/{id}/events | SSE live event stream |
| GET | /api/campaigns/{id}/progress | Aggregated progress counters |

### Contacts & Datasets
| Method | Path | Description |
|---|---|---|
| POST | /api/contacts/upload | Upload CSV / XLSX |
| GET | /api/contacts/datasets | List datasets |
| GET | /api/contacts/datasets/{id} | Dataset details |
| GET | /api/contacts/ | Query contacts (filter, paginate) |
| GET | /api/contacts/{id} | Get single contact |

### Integrations
| Method | Path | Description |
|---|---|---|
| GET | /api/integrations/gmail/auth | Start OAuth flow |
| GET | /api/integrations/gmail/callback | OAuth callback |
| GET | /api/integrations/ | List connected integrations |
| DELETE | /api/integrations/gmail/{id} | Revoke integration |

### Safety
| Method | Path | Description |
|---|---|---|
| GET | /api/safety/suppressions | List suppressed emails |
| POST | /api/safety/suppressions | Add to suppression list |
| DELETE | /api/safety/suppressions/{email} | Remove from suppression list |
| GET | /api/safety/audit | Safety audit log |

### Chat Agent (legacy)
| Method | Path | Description |
|---|---|---|
| POST | /api/chat | Send a chat message |
| GET | /api/chat/history | Get session history |
| POST | /api/escalate | Escalate to human |

---

## Running Tests

```bash
cd backend
source ../venv/bin/activate

# All tests
pytest tests/ -v

# Individual modules
pytest tests/test_contacts.py -v
pytest tests/test_campaigns.py -v
pytest tests/test_personalization.py -v
pytest tests/test_safety.py -v
pytest tests/test_automation.py -v
pytest tests/test_campaign_monitoring.py -v
```

Key scenarios covered:
- Upload 2,000 contacts from CSV → normalise → classify → store
- Create investor / job / internship campaigns
- Generate personalised messages per strategy
- Dry-run produces correct output without sending
- Suppressed recipients never receive messages
- Duplicate contacts and malformed emails are rejected
- Permanent email bounce adds to suppression list
- Stale lease recovery re-queues crashed-worker jobs
- Pause / resume / cancel state transitions
- Revoking Gmail integration blocks further sends
- SSE stream emits all 12 event types
- Idempotency: re-enqueueing jobs is a no-op

---

## Benchmark Results (Chat Agent)

```
======================================================================
EVALUATION METRICS SUMMARY
======================================================================
Total Benchmark Queries   : 20
Intent Classification     : 95.0%  (19/20)
Escalation Accuracy       : 100.0% (20/20)
Tool Selection            : 100.0% (4/4)
RAG Citation Coverage     : 91.7%  (11/12)
======================================================================
```

---

## License

MIT — see LICENSE for details.
