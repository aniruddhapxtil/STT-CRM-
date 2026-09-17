# Voice-to-CRM — End-to-End Prototype

This version extends the original audio → Whisper → LFM2.5 → JSON prototype so validated commands are also written to PostgreSQL.

## Tables

- accounts
- contacts
- leads
- tasks
- notes
- voice_interactions
- audit_logs

The LLM does NOT generate SQL. It produces a validated command, and explicit Python service logic decides which table(s) to write.

## Run

### 1. PostgreSQL

```bash
docker compose up -d
```

### 2. Backend

Windows:

```powershell
cd backend
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
uvicorn main:app --reload --port 8000
```

macOS/Linux:

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn main:app --reload --port 8000
```

Install FFmpeg separately because original Whisper requires it.

### 3. Frontend

Open `frontend/index.html` through a local static server:

```bash
cd frontend
python -m http.server 5173
```

Then open:

http://localhost:5173

## Example

Say:

"Create a lead for ABC Technologies. Rahul Sharma is the CTO. They are interested in AI analytics and the deal value is 25 lakh."

Expected database effect:

1. `accounts`: ABC Technologies
2. `contacts`: Rahul Sharma, CTO, linked to ABC Technologies
3. `leads`: requirement AI Analytics, value 2500000, currency INR
4. `voice_interactions`: transcript + extracted command + timing
5. `audit_logs`: recorded CRM action

A repeated company/contact is resolved instead of blindly duplicating the account/contact.

## Important

The original project description says the first prototype should stop before database writes. This package deliberately adds the database/service layer because the current requirement is to enter extracted fields into the appropriate CRM tables.

For production, add authentication, authorization, confirmation for irreversible writes, Alembic migrations, entity-resolution rules, rate limiting, HTTPS, secrets management, and separate inference workers.
