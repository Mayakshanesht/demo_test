"""
app.py — FastAPI backend for the rwth2125 HPC Assistant.

The user supplies their own Groq API key from the browser (sent per request,
never stored server-side). We proxy to Groq's OpenAI-compatible API and expose
two deterministic tools (salloc + sbatch generators) so the model can produce
guaranteed-correct Slurm commands.

Run:  pip install -r requirements.txt
      python app.py            # serves UI + API on http://localhost:8000
"""

import json
import os

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import slurm_gen as sg
import features as ft
import rag
import bundle
import auth

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
DEFAULT_MODEL = "llama-3.3-70b-versatile"
FRONTEND_DIR = os.path.join(os.path.dirname(__file__), "..", "frontend")

TOOLS = [sg.SALLOC_TOOL_SCHEMA, sg.TOOL_SCHEMA, ft.SCAFFOLD_TOOL_SCHEMA]
SYSTEM = sg.SYSTEM_PROMPT + "\n\nREFERENCE (surface verbatim when relevant):\n" + sg.REFERENCE

app = FastAPI(title="WestAI HPC Assistant")

# Permissive CORS: the Groq key is supplied by the user per request, so there is
# no server secret to protect. Lets the frontend live on a different origin
# (e.g. a separate Vercel static deploy) if you ever split them.
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)


# --- auth dependencies ------------------------------------------------------
def require_user(authorization: str = Header(default="")):
    token = authorization[7:] if authorization.lower().startswith("bearer ") else ""
    payload = auth.verify_token(token)
    if not payload:
        raise HTTPException(status_code=401, detail="Sign in to continue.")
    return payload


def require_admin(user=Depends(require_user)):
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required.")
    return user


class SignupRequest(BaseModel):
    name: str
    email: str
    password: str


class LoginRequest(BaseModel):
    email: str
    password: str


class StatusRequest(BaseModel):
    id: str
    status: str


@app.post("/api/auth/signup")
def auth_signup(req: SignupRequest):
    try:
        return auth.signup(req.name, req.email, req.password)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)


@app.post("/api/auth/login")
def auth_login(req: LoginRequest):
    try:
        return auth.login(req.email, req.password)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)


@app.get("/api/auth/me")
def auth_me(user=Depends(require_user)):
    return auth.get_user(user["uid"]) or JSONResponse({"error": "not found"}, status_code=404)


@app.get("/api/admin/users")
def admin_users(_=Depends(require_admin)):
    return {"users": auth.list_users()}


@app.post("/api/admin/set_status")
def admin_set_status(req: StatusRequest, _=Depends(require_admin)):
    try:
        return auth.set_status(req.id, req.status)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)


class ChatRequest(BaseModel):
    api_key: str
    message: str
    history: list = []          # [{role, content}, ...] prior turns
    model: str = DEFAULT_MODEL
    feature: str = ""           # feature the user selected in the UI (optional)
    user: str = "mayur"         # team member selected in the UI


class ScaffoldRequest(BaseModel):
    feature: str
    user: str = "mayur"
    gpus: int | None = None
    time: str | None = None


class GenerateRequest(BaseModel):
    user: str = "mayur"
    feature: str = "custom"
    mode: str = "development"   # development -> salloc ; production -> sbatch
    devel: bool = False         # development only: 5-min devel-partition test
    gpus: int | None = None
    time: str | None = None


class BundleRequest(BaseModel):
    feature: str
    user: str = "mayur"
    gpus: int | None = None
    time: str | None = None
    remote: str = bundle.DEFAULT_REMOTE
    branch: str | None = None   # defaults to the user's name (per-user branch)
    label: str | None = None    # optional job label for tracking
    run_id: str | None = None   # client may supply; else server generates


def run_tool(name: str, args: dict) -> str:
    """Execute a tool call locally and return its string result."""
    try:
        if name == "generate_slurm_script":
            return sg.generate_script(
                user=args.get("user", "mayur"),
                mode=args.get("mode", "production"),
                gpus=args.get("gpus", 1),
                time=args.get("time", "04:00:00"),
                job_name=args.get("job_name", "rwth2125_job"),
                command=args.get("command", ""),
            )
        if name == "generate_salloc_command":
            return sg.generate_salloc(
                user=args.get("user", "mayur"),
                gpus=args.get("gpus", 1),
                time=args.get("time", "04:00:00"),
                devel=args.get("devel", False),
            )
        if name == "scaffold_feature":
            return ft.scaffold_feature(
                feature=args.get("feature", "custom"),
                user=args.get("user", "mayur"),
                gpus=args.get("gpus"),
                time=args.get("time"),
            )
        return f"(unknown tool {name})"
    except Exception as e:  # surface validation errors to the model
        return f"ERROR: {e}"


def call_groq(api_key: str, model: str, messages: list, use_tools=True):
    payload = {"model": model, "messages": messages, "temperature": 0.2}
    if use_tools:
        payload["tools"] = TOOLS
        payload["tool_choice"] = "auto"
    headers = {"Authorization": f"Bearer {api_key}",
               "Content-Type": "application/json"}
    r = httpx.post(GROQ_URL, json=payload, headers=headers, timeout=120)
    r.raise_for_status()
    return r.json()


@app.post("/api/chat")
def chat(req: ChatRequest, _=Depends(require_user)):
    if not req.api_key.strip():
        return JSONResponse({"error": "Missing Groq API key."}, status_code=400)

    system = SYSTEM

    # RAG: ground the answer on the actual RWTH docs most relevant to this query.
    ctx = rag.retrieve_context(req.message, k=4)
    if ctx:
        system += ("\n\nRETRIEVED DOCUMENTATION (authoritative — prefer this over "
                   "your own memory; cite the [source: ...] when relevant):\n" + ctx)

    if req.feature:
        prof = ft.FEATURES.get(req.feature)
        if prof:
            system += (f"\n\nCURRENT CONTEXT: the user ({req.user}) is working on "
                       f"feature '{req.feature}' ({prof['label']}). Default resources: "
                       f"{prof['gpus']} GPU, {prof['time']}. Tailor scaffolding, "
                       f"resources and examples to this feature and user.")

    messages = [{"role": "system", "content": system}]
    messages += req.history
    messages.append({"role": "user", "content": req.message})

    try:
        data = call_groq(req.api_key, req.model, messages)
        msg = data["choices"][0]["message"]

        # Resolve up to a couple of tool-calling rounds.
        for _ in range(3):
            tool_calls = msg.get("tool_calls")
            if not tool_calls:
                break
            messages.append(msg)
            for tc in tool_calls:
                fn = tc["function"]["name"]
                try:
                    args = json.loads(tc["function"]["arguments"] or "{}")
                except json.JSONDecodeError:
                    args = {}
                result = run_tool(fn, args)
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "name": fn,
                    "content": result,
                })
            data = call_groq(req.api_key, req.model, messages)
            msg = data["choices"][0]["message"]

        return {"reply": msg.get("content", "")}

    except httpx.HTTPStatusError as e:
        detail = e.response.text
        code = e.response.status_code
        hint = "Check that your Groq API key is valid." if code in (401, 403) else ""
        return JSONResponse({"error": f"Groq API error {code}: {detail} {hint}"},
                            status_code=502)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/health")
def health():
    return {"status": "ok", "default_model": DEFAULT_MODEL,
            "team": list(sg.TEAM.keys())}


@app.get("/api/features")
def features_list(_=Depends(require_user)):
    """For the UI dropdown: feature catalog + team members."""
    return {"features": ft.list_features(), "team": list(sg.TEAM.keys())}


@app.post("/api/scaffold")
def scaffold(req: ScaffoldRequest, _=Depends(require_user)):
    """Deterministic form path — no LLM, no API key needed."""
    try:
        return {"script": ft.scaffold_feature(req.feature, req.user,
                                               req.gpus, req.time)}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=400)


@app.post("/api/generate")
def generate(req: GenerateRequest, _=Depends(require_user)):
    """Deterministic, mode-aware: development -> salloc, production -> sbatch."""
    try:
        if req.mode == "production":
            prof = ft.FEATURES.get(req.feature, ft.FEATURES["custom"])
            gpus = req.gpus or prof["gpus"]
            time = req.time or prof["time"]
            script = sg.generate_script(
                user=req.user, mode="production", gpus=gpus, time=time,
                job_name=req.feature, command=prof["command"])
            res = sg.resource_summary("production", gpus, time)
            return {"kind": "sbatch", "script": script, "resources": res}
        # development -> a runnable .sh that starts an interactive salloc session
        gpus = req.gpus or 1
        time = req.time or ("00:30:00" if req.devel else "04:00:00")
        script = sg.generate_dev_script(user=req.user, gpus=gpus, time=time, devel=req.devel)
        res = sg.resource_summary("development", gpus, time, devel=req.devel)
        return {"kind": "salloc", "script": script, "resources": res}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=400)


@app.post("/api/bundle")
def make_bundle(req: BundleRequest, _=Depends(require_user)):
    """Return a downloadable .zip run folder (job + git-push + pull-run)."""
    try:
        name, data, meta = bundle.build_bundle(
            req.feature, req.user, req.gpus, req.time, req.remote,
            req.branch, req.label, req.run_id)
        headers = {
            "Content-Disposition": f'attachment; filename="{name}"',
            "X-Run-Id": meta["run_id"], "X-Label": meta["label"],
            "Access-Control-Expose-Headers": "X-Run-Id, X-Label",
        }
        return Response(content=data, media_type="application/zip", headers=headers)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=400)


# --- Serve the frontend (local/dev; on Vercel static is served separately) --
if os.path.isdir(FRONTEND_DIR):
    @app.get("/")
    def index():
        return FileResponse(os.path.join(FRONTEND_DIR, "index.html"))

    app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
