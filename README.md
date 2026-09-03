# Healthcare SaaS Authentication & Onboarding Backend

Production-grade FastAPI authentication and doctor verification backend for a healthcare SaaS platform with role-based access control, JWT authentication with refresh token rotation, doctor onboarding/document verification, and SaaS Admin approval workflows.

## Tech Stack

| Technology | Purpose |
|---|---|
| Python 3.12+ | Runtime |
| FastAPI | Web framework |
| PostgreSQL 16 | Database |
| SQLAlchemy 2.x (async) | ORM |
| Alembic | Database migrations |
| Pydantic v2 | Validation & serialization |
| Argon2id | Password hashing |
| JWT (python-jose) | Token authentication |
| asyncpg | Async PostgreSQL driver |
| python-multipart | File upload processing |
| Docker | Containerization |

## Project Structure

```
app/
├── main.py                     # FastAPI application entry point
├── core/
│   ├── config.py               # Pydantic Settings (env-driven)
│   ├── database.py             # Async SQLAlchemy engine & session
│   ├── file_upload.py          # Secure file storage and validation
│   ├── security.py             # Argon2id hashing & JWT utilities
│   ├── exceptions.py           # Custom HTTP exceptions
│   └── logging.py              # Structured logging with sensitive filter
├── models/
│   ├── enums.py                # UserRole, UserStatus, DocumentType enums
│   ├── base.py                 # TimestampMixin
│   ├── user.py                 # User model (auth-only)
│   ├── refresh_token.py        # Refresh token tracking
│   ├── doctor_profile.py       # Doctor professional data & review feedback
│   └── doctor_document.py      # Uploaded document metadata
├── schemas/
│   ├── auth.py                 # Auth request/response schemas
│   ├── user.py                 # User & /me profile schemas
│   ├── doctor.py               # Doctor profile, documents, and status schemas
│   └── admin.py                # Admin review & pending queues schemas
├── repositories/
│   ├── user_repository.py      # User data access
│   ├── token_repository.py     # Refresh token data access
│   └── doctor_repository.py    # Doctor profile & documents data access
├── services/
│   ├── auth_service.py         # Auth business logic
│   ├── admin_service.py        # Admin auth & doctor review logic
│   └── doctor_service.py       # Doctor profile & document upload logic
├── api/
│   ├── deps.py                 # Auth dependencies, doctor access & RBAC
│   └── v1/
│       ├── router.py           # V1 router aggregator
│       ├── auth.py             # Public auth endpoints & /me
│       ├── admin_auth.py       # Admin login endpoint
│       ├── doctor.py           # Doctor onboarding & documents
│       ├── admin_doctors.py    # SaaS Admin review & feedback
│       └── health.py           # Health check endpoints
└── scripts/
    └── create_admin.py         # CLI admin creation script

migrations/
├── env.py                      # Async Alembic environment
├── script.py.mako              # Migration template
└── versions/
    ├── 0001_initial_authentication_schema.py
    └── 0002_doctor_onboarding_fields.py

tests/
├── conftest.py                 # Fixtures & test helpers
├── test_signup.py              # Signup endpoint tests
├── test_login.py               # Login endpoint tests
├── test_admin.py               # Admin auth tests
├── test_jwt.py                 # JWT handling tests
├── test_logout.py              # Logout & token revocation tests
├── test_doctor_profile.py      # Doctor profile & document upload tests
└── test_doctor_review.py       # Admin review & feedback tests
```

## Quick Start

### 1. Clone & Create Virtual Environment

```bash
git clone <repository-url>
cd <project-directory>
python -m venv venv

# Windows
venv\Scripts\activate

# macOS/Linux
source venv/bin/activate
```

### 2. Install Dependencies

```bash
pip install -r requirements.txt
```

### 3. Environment Setup

```bash
cp .env.example .env
```

**Important**: Set `JWT_SECRET_KEY` in `.env`:

```bash
python -c "import secrets; print(secrets.token_urlsafe(64))"
```

### 4. Database Setup & Migration

```bash
# Apply migrations
alembic upgrade head
```

### 5. Create Initial SaaS Admin

```bash
python -m app.scripts.create_admin
```

### 6. Start the Server

```bash
uvicorn app.main:app --reload
```

Documentation: **http://localhost:8000/docs** (Swagger UI)

---

## Complete API Endpoints

### 🩺 Public Authentication

| Method | Path | Description | Access |
|---|---|---|---|
| `POST` | `/api/v1/auth/signup` | Register patient or doctor | Public |
| `POST` | `/api/v1/auth/login` | Login (patient or doctor) | Public |
| `POST` | `/api/v1/auth/refresh` | Refresh access token | Public |
| `POST` | `/api/v1/auth/logout` | Revoke refresh token | Public |
| `GET` | `/api/v1/auth/me` | Current user profile & doctor feedback | Authenticated |

### 👨‍⚕️ Doctor Onboarding & Documents

| Method | Path | Description | Access |
|---|---|---|---|
| `GET` | `/api/v1/doctors/profile` | Get doctor profile & documents | Doctor |
| `PUT` | `/api/v1/doctors/profile` | Update professional details | Doctor |
| `POST` | `/api/v1/doctors/documents` | Upload verification document (PDF/PNG/JPG) | Doctor |
| `GET` | `/api/v1/doctors/documents` | List uploaded documents | Doctor |
| `DELETE` | `/api/v1/doctors/documents/{id}` | Delete uploaded document | Doctor |
| `POST` | `/api/v1/doctors/submit-application` | Submit application for admin review | Doctor |
| `GET` | `/api/v1/doctors/status` | Check review status and feedback | Doctor |

### 🛡️ SaaS Admin Authentication & Doctor Review

| Method | Path | Description | Access |
|---|---|---|---|
| `POST` | `/api/v1/admin/auth/login` | SaaS Admin login | Public |
| `GET` | `/api/v1/admin/doctors/pending` | List pending doctor applications | SaaS Admin |
| `GET` | `/api/v1/admin/doctors/{id}` | View doctor details & documents | SaaS Admin |
| `POST` | `/api/v1/admin/doctors/{id}/review` | Approve or Reject with Feedback | SaaS Admin |

### 💓 Health

| Method | Path | Description | Access |
|---|---|---|---|
| `GET` | `/health` | Liveness check | Public |
| `GET` | `/health/ready` | Database readiness check | Public |

---

## Doctor Onboarding & Approval Workflow

```text
Doctor Signup
      ↓
Doctor updates profile (Name, Specialization, License, Experience, Qualification, Bio)
      ↓
Doctor uploads verification documents (Medical License, Degrees, ID Proof)
      ↓
Doctor submits application  (POST /api/v1/doctors/submit-application)
      ↓
SaaS Admin views pending queue  (GET /api/v1/admin/doctors/pending)
      ↓
SaaS Admin reviews details & files  (GET /api/v1/admin/doctors/{id})
      ↓
      ┌─────────────────────────────────┐
      │                                 │
   APPROVE                           REJECT (with mandatory feedback)
      │                                 │
      ↓                                 ↓
 Status becomes 'active'          Status becomes 'rejected'
 Doctor has full dashboard access   Admin feedback visible on doctor profile
                                        │
                                        ↓
                                  Doctor inspects feedback,
                                  fixes details/documents,
                                  and re-submits application!
```

---

## Testing

```bash
# Run all tests
pytest -v

# Run specific suite
pytest tests/test_doctor_profile.py -v
pytest tests/test_doctor_review.py -v
```

## Security Features

- **Argon2id** password hashing
- **JWT token rotation** and SHA-256 hashed database storage
- **Rejection feedback requirement**: Admin cannot reject without giving concrete feedback
- **Re-submission lifecycle**: Clears feedback and resets application timestamp on re-submit
- **Strict file validation**: Content-type check, maximum 10MB file limit, UUID storage naming
- **Role-Based Access Control (RBAC)** enforced at endpoint level
