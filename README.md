# Healthcare SaaS Authentication Backend

Production-grade FastAPI authentication system for a healthcare SaaS platform with role-based access control, JWT authentication with refresh token rotation, and clean architectural separation.

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
| Docker | Containerization |

## Project Structure

```
app/
├── main.py                     # FastAPI application entry point
├── core/
│   ├── config.py               # Pydantic Settings (env-driven)
│   ├── database.py             # Async SQLAlchemy engine & session
│   ├── security.py             # Argon2id hashing & JWT utilities
│   ├── exceptions.py           # Custom HTTP exceptions
│   └── logging.py              # Structured logging with sensitive filter
├── models/
│   ├── enums.py                # UserRole, UserStatus enums
│   ├── base.py                 # TimestampMixin
│   ├── user.py                 # User model (auth-only)
│   ├── refresh_token.py        # Refresh token tracking
│   └── doctor_profile.py       # Skeleton for future doctor data
├── schemas/
│   ├── auth.py                 # Request/response schemas with validation
│   └── user.py                 # User response schemas
├── repositories/
│   ├── user_repository.py      # User data access
│   └── token_repository.py     # Refresh token data access
├── services/
│   ├── auth_service.py         # Auth business logic
│   └── admin_service.py        # Admin auth business logic
├── api/
│   ├── deps.py                 # Auth dependencies & RBAC
│   └── v1/
│       ├── router.py           # V1 router aggregator
│       ├── auth.py             # Public auth endpoints
│       ├── admin_auth.py       # Admin auth endpoints
│       └── health.py           # Health check endpoints
└── scripts/
    └── create_admin.py         # CLI admin creation script

migrations/
├── env.py                      # Async Alembic environment
├── script.py.mako              # Migration template
└── versions/
    └── 0001_initial_auth.py    # Initial schema migration

tests/
├── conftest.py                 # Fixtures & test helpers
├── test_signup.py              # Signup endpoint tests
├── test_login.py               # Login endpoint tests
├── test_admin.py               # Admin auth tests
├── test_jwt.py                 # JWT handling tests
└── test_logout.py              # Logout & token revocation tests
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
# Copy the example and edit with your values
cp .env.example .env
```

**Important**: Change `JWT_SECRET_KEY` to a cryptographically random string:

```bash
python -c "import secrets; print(secrets.token_urlsafe(64))"
```

### 4. Start PostgreSQL

**Option A: Docker Compose** (recommended)

```bash
docker-compose up db -d
```

**Option B: Local PostgreSQL**

Create the database:

```sql
CREATE DATABASE healthcare_saas;
```

### 5. Run Database Migrations

```bash
alembic upgrade head
```

### 6. Create SaaS Admin

```bash
python -m app.scripts.create_admin
```

The script will prompt for email and password (hidden input).

### 7. Start the Server

```bash
# Development
uvicorn app.main:app --reload

# Production
uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 4
```

### 8. Open API Documentation

Visit: http://localhost:8000/docs (Swagger UI)

## Docker Deployment

```bash
# Start everything (PostgreSQL + FastAPI)
docker-compose up --build

# Run migrations
docker-compose exec app alembic upgrade head

# Create admin
docker-compose exec -it app python -m app.scripts.create_admin
```

## API Endpoints

### Health

| Method | Path | Description |
|---|---|---|
| `GET` | `/health` | Liveness check |
| `GET` | `/health/ready` | Readiness check (DB connectivity) |

### Public Authentication

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/v1/auth/signup` | Register patient or doctor |
| `POST` | `/api/v1/auth/login` | Login (patient or doctor) |
| `POST` | `/api/v1/auth/refresh` | Refresh access token |
| `POST` | `/api/v1/auth/logout` | Revoke refresh token |

### Admin Authentication

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/v1/admin/auth/login` | SaaS Admin login |

## How It Works

### Signup Flow

**Patient**: `POST /api/v1/auth/signup` → email validated → password validated & hashed (Argon2id) → user created with `status=active` → success response.

**Doctor**: Same flow but `status=pending`. Doctor cannot login until an admin approves (changes status to `active`). An empty `doctor_profile` row is created for future professional data.

**SaaS Admin**: Cannot signup via API. Created only via CLI: `python -m app.scripts.create_admin`.

### Login Flow

1. Client sends email + password to `POST /api/v1/auth/login`
2. Server looks up user by email (case-insensitive)
3. Server verifies password against Argon2id hash
4. Server checks `status == active` and `is_active == True`
5. Server generates access token (short-lived) + refresh token (longer-lived)
6. Refresh token SHA-256 hash stored in `refresh_tokens` table
7. Returns both tokens + user info (never password/hash)

### JWT Architecture

- **Access Token**: Short-lived (default 15 min). Contains `sub`, `role`, `type=access`, `iat`, `exp`, `jti`.
- **Refresh Token**: Longer-lived (default 7 days). Contains `sub`, `role`, `type=refresh`, `iat`, `exp`, `jti`.
- Tokens are signed with HS256 using `JWT_SECRET_KEY`.
- Only SHA-256 hashes of refresh tokens are stored — never raw tokens.

### Token Refresh & Rotation

1. Client sends refresh token to `POST /api/v1/auth/refresh`
2. Server validates JWT signature and expiry
3. Server looks up token hash in DB
4. If token is revoked → **all user tokens revoked** (theft detection)
5. Old token revoked, new pair issued
6. New refresh token hash stored

### Doctor Status Architecture

```
Signup → status=pending → Admin reviews → status=active (can login)
                                        → status=rejected (cannot login)
                              Anytime   → status=suspended (cannot login)
```

Login checks: `pending → 403` | `rejected → 403` | `suspended → 403` | `active → 200`

### Role-Based Access Control (RBAC)

```python
# Protect an endpoint by role
from app.api.deps import require_role
from app.models.enums import UserRole

@router.get("/doctor-dashboard")
async def doctor_dashboard(user = Depends(require_role(UserRole.DOCTOR))):
    ...

@router.get("/admin-panel")
async def admin_panel(user = Depends(require_role(UserRole.SAAS_ADMIN))):
    ...
```

## Testing

### Setup Test Database

```bash
# Create test database
psql -U postgres -c "CREATE DATABASE healthcare_saas_test;"
```

Set `TEST_DATABASE_URL` in your `.env`:

```env
TEST_DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/healthcare_saas_test
```

### Run Tests

```bash
# All tests
pytest -v

# Specific module
pytest tests/test_signup.py -v

# With coverage
pytest --cov=app --cov-report=term-missing
```

## Database Migrations

```bash
# Create a new migration
alembic revision --autogenerate -m "description of changes"

# Apply all migrations
alembic upgrade head

# Rollback one step
alembic downgrade -1

# View current revision
alembic current
```

## Security Features

- **Argon2id** password hashing (memory-hard, timing-attack resistant)
- **JWT** with short-lived access tokens + refresh token rotation
- **SHA-256 hashed** refresh tokens in database (defense-in-depth)
- **Theft detection**: reuse of revoked refresh token triggers full session revocation
- **Timing-safe** login: dummy hash on wrong email prevents user enumeration
- **Generic error messages**: same error for wrong email and wrong password
- **Database constraints**: unique email enforced at PostgreSQL level
- **SQL injection protection**: SQLAlchemy parameterized queries only
- **CORS**: environment-driven, no wildcard in production
- **No hard-coded secrets**: all sensitive config via environment variables
- **Sensitive field filtering** in logs: passwords, tokens, credentials never logged
- **Non-root Docker** user in production container

## Future Extensibility

The architecture is designed to support:

- Doctor approval/rejection workflow (change `user.status`)
- Doctor document uploads (extend `doctor_profiles`)
- Patient profiles (new `patient_profiles` table)
- Appointments, medical reports, chat
- Email verification, password reset, 2FA
- Redis rate limiting (add middleware)
- Audit logs (new model/service)
- Frontend dashboards (consume these APIs)

## License

Proprietary — All rights reserved.
