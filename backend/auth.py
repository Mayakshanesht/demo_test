"""
auth.py — real auth for the WestAI HPC Assistant.

Sign-up / sign-in / admin-approval workflow with roles.
Passwords: pbkdf2-hmac-sha256 + per-user salt. Sessions: HMAC-signed tokens.

Storage is DUAL-BACKEND so it works both locally and on Vercel:
  * If DATABASE_URL (or POSTGRES_URL) is set  -> PostgreSQL via psycopg
    (use this on Vercel: Vercel Postgres / Neon / Supabase).
  * Otherwise                                 -> local SQLite file (dev, ./run.sh).
The function API is identical for both.

Required Vercel env vars: DATABASE_URL, SESSION_SECRET, (optional) ADMIN_EMAILS.
"""

import base64
import hashlib
import hmac
import json
import os
import sqlite3
import time
import uuid

PG_URL = os.environ.get("DATABASE_URL") or os.environ.get("POSTGRES_URL")
USE_PG = bool(PG_URL)
if USE_PG:
    import psycopg
    from psycopg.rows import dict_row

_HERE = os.path.dirname(__file__)
DB_PATH = os.environ.get(
    "WESTAI_DB",
    "/tmp/westai_users.db" if os.environ.get("VERCEL")
    else os.path.join(_HERE, "data", "users.db"))

SECRET = os.environ.get("SESSION_SECRET", "dev-secret-change-me").encode()
TOKEN_TTL = 7 * 24 * 3600
PBKDF2_ROUNDS = 120_000
ADMIN_EMAILS = {e.strip().lower() for e in
                os.environ.get("ADMIN_EMAILS", "").split(",") if e.strip()}

_INIT = False


# --- storage layer (Postgres or SQLite) ------------------------------------
def _connect():
    if USE_PG:
        return psycopg.connect(PG_URL, row_factory=dict_row)
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def _ph(sql):
    return sql.replace("?", "%s") if USE_PG else sql


def _exec(sql, params=(), fetch=None):
    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute(_ph(sql), params)
        out = cur.fetchone() if fetch == "one" else cur.fetchall() if fetch == "all" else None
        conn.commit()
        return out
    finally:
        conn.close()


def init_db():
    global _INIT
    if _INIT:
        return
    created = "DOUBLE PRECISION" if USE_PG else "REAL"
    _exec(f"""CREATE TABLE IF NOT EXISTS users(
        id TEXT PRIMARY KEY, name TEXT, email TEXT UNIQUE, pw TEXT,
        role TEXT DEFAULT 'user', status TEXT DEFAULT 'pending',
        created {created})""")
    _INIT = True


# --- password hashing -------------------------------------------------------
def _hash_pw(password, salt=None):
    salt = salt or os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ROUNDS)
    return f"{base64.b64encode(salt).decode()}${base64.b64encode(dk).decode()}"


def _check_pw(password, stored):
    try:
        salt = base64.b64decode(stored.split("$", 1)[0])
        return hmac.compare_digest(_hash_pw(password, salt), stored)
    except Exception:
        return False


# --- tokens -----------------------------------------------------------------
def _b64(b):
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def _unb64(s):
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def make_token(uid, role):
    payload = {"uid": uid, "role": role, "exp": int(time.time()) + TOKEN_TTL}
    body = _b64(json.dumps(payload).encode())
    sig = _b64(hmac.new(SECRET, body.encode(), hashlib.sha256).digest())
    return f"{body}.{sig}"


def verify_token(token):
    try:
        body, sig = token.split(".", 1)
        good = _b64(hmac.new(SECRET, body.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(sig, good):
            return None
        payload = json.loads(_unb64(body))
        return payload if payload.get("exp", 0) >= time.time() else None
    except Exception:
        return None


# --- user operations --------------------------------------------------------
def _public(r):
    return {"id": r["id"], "name": r["name"], "email": r["email"],
            "role": r["role"], "status": r["status"], "created": r["created"]}


def signup(name, email, password):
    name = (name or "").strip()
    email = (email or "").strip().lower()
    if not name or "@" not in email or len(password or "") < 6:
        raise ValueError("Provide a name, a valid email, and a 6+ character password.")
    init_db()
    if _exec("SELECT id FROM users WHERE email=?", (email,), "one"):
        raise ValueError("An account with that email already exists.")
    first = (_exec("SELECT COUNT(*) AS n FROM users", (), "one")["n"] == 0)
    admin = first or email in ADMIN_EMAILS
    role = "admin" if admin else "user"
    status = "approved" if admin else "pending"
    _exec("INSERT INTO users(id,name,email,pw,role,status,created) VALUES(?,?,?,?,?,?,?)",
          (uuid.uuid4().hex, name, email, _hash_pw(password), role, status, time.time()))
    return {"status": status, "role": role,
            "message": ("Your admin account is ready — sign in." if admin else
                        "Account created. An admin must approve you before you can sign in.")}


def login(email, password):
    init_db()
    email = (email or "").strip().lower()
    row = _exec("SELECT * FROM users WHERE email=?", (email,), "one")
    if not row or not _check_pw(password, row["pw"]):
        raise ValueError("Invalid email or password.")
    if row["status"] != "approved":
        raise ValueError("Your account is pending admin approval." if row["status"] == "pending"
                         else "Your account access was declined.")
    return {"token": make_token(row["id"], row["role"]), "user": _public(row)}


def get_user(uid):
    row = _exec("SELECT * FROM users WHERE id=?", (uid,), "one")
    return _public(row) if row else None


def list_users():
    init_db()
    return [_public(r) for r in _exec("SELECT * FROM users ORDER BY created", (), "all")]


def set_status(uid, status):
    if status not in ("approved", "rejected", "pending"):
        raise ValueError("bad status")
    _exec("UPDATE users SET status=? WHERE id=?", (status, uid))
    return get_user(uid)


def delete_user(uid):
    _exec("DELETE FROM users WHERE id=?", (uid,))
    return True
