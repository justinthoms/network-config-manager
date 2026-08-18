# Network Config Manager

Phase 1 local-development foundation for a multi-vendor network configuration backup platform.

## Stack
- FastAPI
- SQLAlchemy
- PostgreSQL-ready database configuration (SQLite default for local testing)
- JWT authentication
- RBAC foundation
- Netmiko vendor connectivity
- Huawei / Juniper / Arista vendor adapters
- Simple web UI served by FastAPI

## Quick start

### 1. Create virtual environment
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt
```

### 2. Configure
```bash
cp backend/.env.example backend/.env
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
# Put the generated value into DEVICE_ENCRYPTION_KEY in backend/.env

```

### 3. Run
```bash
uvicorn app.main:app --app-dir backend --reload --host 0.0.0.0 --port 8000
```

Open:
- http://127.0.0.1:8000
- http://127.0.0.1:8000/docs

Device SSH passwords are encrypted with Fernet using DEVICE_ENCRYPTION_KEY.

Default local admin:
- username: `admin`
- password: `admin123`
Change it immediately in a real deployment.

## Backup directory

Backups are stored as:

`backups/YYYY/MM/DD/HOSTNAME/TIMESTAMP.conf`

Example:

`backups/2026/08/18/TSR-MX304-01/20260818_120000.conf`

## Next phases
- PostgreSQL + Redis
- Celery scheduler/worker
- Full React frontend
- Google Drive OAuth
- Config diff/versioning
- Advanced RBAC
- Separate license server
- Docker/GitHub Actions
