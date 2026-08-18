#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"
python3 -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt
cp -n backend/.env.example backend/.env 2>/dev/null || true
uvicorn app.main:app --app-dir backend --reload --host 0.0.0.0 --port 8000
