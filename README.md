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

## Deploy to Vercel (frontend **and** backend)

Vercel runs the backend as a **Python serverless function** — our backend is
stateless and fast, so it fits serverless perfectly (no separate host needed).

- `api/index.py` exposes the FastAPI app; `vercel.json` routes all requests to it
  and bundles `backend/` + `frontend/`; root `requirements.txt` lists deps.

```bash
npm i -g vercel
cd webapp
vercel            # preview deploy
vercel --prod     # production
```
Set the Vercel **Root Directory** to `webapp/`. Test locally with `vercel dev`.

If you ever split them (frontend on Vercel, backend elsewhere like Render/Railway/
the HPC), CORS is already enabled — just point the frontend `fetch` calls at the
backend URL.

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
