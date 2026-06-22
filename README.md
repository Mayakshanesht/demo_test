# WestAI HPC Assistant

An LLM-powered assistant so the team **never has to memorise Slurm/HPC commands**.
Pick who you are and the feature you're working on, ask in plain English, and get
a **downloadable `.sh`** you can commit to git and run on the RWTH **CLAIX-2023**
cluster (project `rwth2125`). Each user unlocks it with their own **Groq API key**.

Four tabs:
- **Generate** — pick user, feature, mode, GPUs, time → get the right command:
  - **Development → `salloc`** delivered as a runnable `.sh` you start directly
    (`chmod +x dev_session.sh && ./dev_session.sh`), *not* sbatch.
  - **Production → `sbatch run.sh`** for training / longer jobs.
  - **📦 Download bundle** — a uniquely-named `.zip` run (job + git-push + HPC pull-run).
- **Assistant** — Groq chat grounded by RAG on the RWTH docs (storage, transfer, modules, GPU/CUDA).
- **History** — every run you generated: machine, VRAM, RAM, duration, run-id.
- **Admin** — totals, GPU-hours requested, per-user breakdown, team & caps.

The `#SBATCH` lines come from a deterministic generator exposed to the model as a
**tool**, so partition (`c23g`), account (`rwth2125`) and sizing are always correct —
and oversized requests are **capped** (see Safety caps).

## How team members use it (zero commands to learn)

1. Open the app, choose **You** (Mayur/Madhava) and the **Feature** in the top bar.
2. Either hit **🛠 Scaffold** (form path, no key) or paste your **Groq key** and chat.
3. Click **⬇ .sh** on any script to download it.
4. Drop it in your feature repo → `git commit` → `git push`.
5. On an HPC login node: `cd /work/rwth2125/repos/<repo> && git pull && sbatch run.sh`
   (the assistant prints these exact lines for you).

## Safety caps (enforced server-side)

| Limit | Value |
|-------|-------|
| Max GPUs | 4 (one c23g node) |
| Max wall time | 24 h (devel: 1 h) |

Oversized requests are refused with a clear message; the assistant suggests the
max allowed or a chain job. RAM is derived from GPU count, so capping GPUs caps RAM.

## Architecture

```
Browser (frontend/index.html)        Backend (FastAPI)             Groq API
  pick user + feature   ──/api/scaffold──▶ deterministic generators  (no LLM)
  paste Groq key, chat  ──/api/chat──────▶ system prompt + 3 tools ──▶ llama-3.3-70b
  ⬇ download .sh        ◀── reply ─────────  runs tools locally     ◀── tool_calls
```

Tools: `generate_salloc_command`, `generate_slurm_script`, `scaffold_feature`.
The key is sent per request, used only to call Groq, never stored or logged.

## Run locally

```bash
cd webapp
./run.sh                 # builds a venv, serves http://localhost:8000
```
Get a free Groq key: <https://console.groq.com/keys>

## Accounts & access (sign up / sign in / approval)

The app is gated behind login. Flow:
- **Sign up** (name, email, password) → account is created **pending**.
- An **admin approves** it (Admin tab → User access → Approve) → the person can sign in.
- The **first account to sign up becomes the admin** automatically. You can also
  pre-mark admins with the `ADMIN_EMAILS` env var (comma-separated).

Passwords are pbkdf2-hashed; sessions are HMAC-signed tokens (7-day expiry) kept
in the browser. All work endpoints require a valid token; approvals require admin.

## Database

Storage is **dual-backend** (same code both ways):
- **Local dev** → SQLite at `backend/data/users.db` automatically (no setup).
- **Production / Vercel** → **PostgreSQL** via the `DATABASE_URL` env var
  (Vercel's filesystem is ephemeral, so you must use a hosted DB there).

## Deploy to Vercel (frontend **and** backend, with Postgres)

Vercel runs the backend as a **Python serverless function**. `api/index.py`
exposes the FastAPI app; `vercel.json` routes all requests to it and bundles
`backend/` + `frontend/`; root `requirements.txt` lists deps (incl. `psycopg`).

1. **Create a database** — in the Vercel dashboard: Storage → **Postgres** (Neon).
   It sets `DATABASE_URL` / `POSTGRES_URL` for you. (Supabase/Neon also work.)
2. **Set env vars** (Project → Settings → Environment Variables):
   - `DATABASE_URL` — the Postgres connection string (auto if you used Vercel Postgres)
   - `SESSION_SECRET` — a long random string (sign tokens)
   - `ADMIN_EMAILS` — optional, e.g. `mayur@cloudbee.io`
   - `VERCEL` — set to `1`
3. **Deploy**:
   ```bash
   npm i -g vercel
   cd webapp
   vercel --prod     # set Root Directory to webapp/
   ```
The users table is created automatically on first request. Test locally first
with `vercel dev` (it loads `.env`).

If you ever split frontend/backend, CORS is already enabled — point the frontend
`fetch` calls at the backend URL.

## Push to GitHub

```bash
cd webapp
git init && git add . && git commit -m "WestAI HPC Assistant"
gh repo create westai-hpc-assistant --private --source=. --push
```
(`.gitignore` already excludes `.venv/`, `__pycache__/`, `.vercel/`.)

## Files

| File | Role |
|------|------|
| `backend/app.py` | FastAPI server, Groq proxy, tool loop, `/api/chat` `/api/scaffold` `/api/features` |
| `backend/slurm_gen.py` | salloc/sbatch generators, safety caps, system prompt + reference |
| `backend/features.py` | Feature catalog + repo scaffolder |
| `frontend/index.html` | Chat UI: user/feature pickers, key gate, copy + ⬇ .sh download |
| `api/index.py`, `vercel.json`, `requirements.txt` | Vercel serverless deploy |
| `run.sh` | Local venv bootstrap + launch |

## Team mapping (edit in `backend/slurm_gen.py`)

| Name | HPC user |
|------|----------|
| mayur | kbp11750 |
| madhava | xco30720 |
