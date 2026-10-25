# RupeeShield — Installation Guide

This guide installs **RupeeShield** (Real-Time Fraud Detection & Anomaly
Intelligence Platform) on a fresh machine. It is written so a teammate can copy the
commands top to bottom and end up with a working app.

Everything runs **locally**: no paid service, no API key, no external database. All
data the platform processes is synthetic.

When you finish, you will have two things running:

| What | URL |
|---|---|
| API (FastAPI) | http://127.0.0.1:8000 |
| API docs (Swagger) | http://127.0.0.1:8000/docs |
| Dashboard (React) | http://localhost:5173 |

---

## 1. Requirements

| Requirement | Version | Notes |
|---|---|---|
| Python | **3.12** (3.12 or newer works) | CI and the Docker image pin 3.12 |
| Node.js + npm | **20** | For the React dashboard |
| Git | any | To clone the repository |
| Docker + Compose | optional | Only if you want to run the API in a container instead of a venv |

Check your versions:

```bash
python --version
node --version
npm --version
git --version
```

No Java, no message broker, no external database and no API key are needed.

---

## 2. Get the code

```bash
git clone <repository-url>
cd Real-Time-Fraud-Detection-Anomaly-Intelligence-Platform
```

All commands below are run **from the repository root** unless stated otherwise.

---

## 3. Backend setup

### 3.1 Create and activate a virtual environment

**Windows (PowerShell):**

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
```

**macOS / Linux:**

```bash
python -m venv .venv
source .venv/bin/activate
```

> If PowerShell blocks the activation script, allow it for the current user once:
> `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`

### 3.2 Install Python dependencies

```bash
pip install -r requirements.txt
```

### 3.3 Optional: configuration

The app runs with **no configuration at all**. If you want to change anything
(thresholds, ports, demo logins, session lifetime), copy the template:

**Windows:**

```powershell
Copy-Item .env.example .env
```

**macOS / Linux:**

```bash
cp .env.example .env
```

`.env` is git-ignored. Every setting is prefixed `RUPESHIELD_`; the full list is in
`.env.example` and in the main `README.md` (section 13).

---

## 4. Prepare data and models

These steps create the local database contents the app reads.

```bash
# 4.1 Generate the synthetic ML dataset (fast sample; ~300 accounts / 12k rows)
python scripts/generate_dataset.py --sample

# ...or the full default dataset (4,000 accounts / 250,000 rows)
# python scripts/generate_dataset.py

# 4.2 Train the detection models (writes artefacts into models/)
python -m app.ml.train

# 4.3 Seed the demo bank: customer, accounts, payees, history, one scored
#     probe payment and its incident. Safe to re-run — it is a no-op once done.
python -m app.db.seed
```

Notes:

- The repository already ships **pre-trained artefacts** in `models/`, so step 4.2 is
  only required after you change the dataset or the feature set. Step 4.1 *is*
  required if you use `--sample`, so the models match the data you generated.
- Step 4.3 is what gives the sign-in page a customer with accounts and a ledger to
  show. Without it you can sign in, but the banking pages will be empty.

---

## 5. Start the backend

In a terminal (with the virtual environment active):

```bash
python -m uvicorn app.main:app --reload --port 8000
```

Verify it is up:

```bash
# Windows
Invoke-WebRequest http://127.0.0.1:8000/api/health

# macOS / Linux
curl http://127.0.0.1:8000/api/health
```

You should get HTTP 200 with a small JSON body. Leave this terminal running.

---

## 6. Start the dashboard

Open a **second** terminal (the backend must stay running):

```bash
npm --prefix frontend install
npm --prefix frontend run dev
```

The Vite dev server runs on **http://localhost:5173** and proxies `/api` to
`http://127.0.0.1:8000`.

---

## 7. Sign in

The dashboard opens on the sign-in page. Use one of the **fictional demo logins**
(created in step 4.3):

| Role | Email | Password |
|---|---|---|
| Customer | `priya.sharma@rupeeshield.demo` | `demo-customer-2026` |
| Security Analyst | `analyst@rupeeshield.demo` | `demo-analyst-2026` |

- The **Customer** sees the Banking workspace: dashboard, transaction history,
  simulated transfer and Account Security Center.
- The **Security Analyst** sees the Security Operations console (overview, attack
  simulator, live monitoring, explorer, network, incidents, defense comparison, ML
  evaluation, settings).

These are local demo credentials only. They are created only while
`RUPESHIELD_DEMO_MODE_ENABLED` is `true` (the default) and are stored as hashes.

---

## 8. Verify the installation (optional)

```bash
# Backend test suite
python -m pytest -q

# Frontend type-check, unit tests and production build
npm --prefix frontend run typecheck
npm --prefix frontend run test
npm --prefix frontend run build
```

Suggested first run in the UI (proves the two workspaces share one backend):
sign in as the **Customer**, make a simulated transfer, then sign in as the
**Analyst** and find the same transaction on the Transaction Explorer.

---

## 9. Run with Docker (alternative to steps 3 and 5)

If you prefer not to create a Python virtual environment, run the API in a
container. The dashboard still runs on the host.

```bash
docker compose up --build
```

- API on http://localhost:8000 (docs at `/docs`).
- The image contains the app and the pre-trained `models/` artefacts.
- SQLite lives inside the container; it is recreated on `docker compose down`. Mount
  a volume at `/app/data` and set `RUPESHIELD_DATABASE_URL` if you want runs to
  persist.
- Then start the dashboard from the repo root as in step 6.

---

## 10. Common problems

| Problem | Fix |
|---|---|
| `python` / `pip` not found | Install Python 3.12+ and make sure it is on your `PATH`. On Windows, re-run the installer and tick "Add Python to PATH". |
| PowerShell refuses to activate the venv | `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`, then reactivate. |
| `npm install` fails | Node 20 is required. Check `node --version`; use `nvm`/`nvm-windows` if you need to switch. |
| `Port 8000 is already in use` | Another process holds the port. Stop it, or start on another port: `python -m uvicorn app.main:app --port 8001`, then point the frontend at it with `RUPESHIELD_API_URL=http://127.0.0.1:8001`. |
| Dashboard loads but every request fails | The backend is not running. Start it (step 5) and confirm `http://127.0.0.1:8000/api/health` returns 200. |
| Sign-in page has no demo buttons | Demo mode is off (`RUPESHIELD_DEMO_MODE_ENABLED=false`) — set it back to `true` and restart the API. |
| `Incorrect email or password` for a demo login | The demo bank was not seeded. Run `python -m app.db.seed` (safe to re-run). |
| Customer dashboard shows no accounts | Same cause — run `python -m app.db.seed`, then sign in again. |
| Decisions are labelled "rule-only" / models unavailable | Train the models: `python scripts/generate_dataset.py --sample` then `python -m app.ml.train`. |
| Requests start returning `429` | The local rate limiter is deliberately low; wait, or raise `RUPESHIELD_RATE_LIMIT_REQUESTS_PER_MINUTE`. |

---

## 11. Reset to a clean state

To wipe the local database, generated data and installed dependencies and start
over, delete the following (they are all git-ignored / regenerable):

```text
rupeeshield.db      # local SQLite database
data/               # generated dataset CSVs
models/             # trained artefacts (checked in — restore with `git checkout -- models`)
.venv/              # Python virtual environment
frontend/node_modules/   # frontend dependencies
```

Then repeat steps 3–6.

---

## 12. Next steps

- Feature and architecture overview: `README.md`.
- Environment variables and thresholds: `README.md`, section 13.
- API surface: `README.md`, section 16, or the live docs at
  http://127.0.0.1:8000/docs.
- Testing details: `README.md`, section 18.
