import os
import json
import sqlite3
import secrets
import hashlib
import hmac
import time
from datetime import datetime, timezone, timedelta
from functools import wraps
from urllib.parse import urlparse

from flask import (
    Flask, request, jsonify, session, redirect,
    url_for, render_template_string, abort, make_response
)
from werkzeug.security import generate_password_hash, check_password_hash

# ============================================================
# PRIVATE MOD MENU CONTROL SERVER
# ============================================================
# Requirements:
#   pip install flask
#
# Environment variables:
#   ADMIN_USER=your_admin_username
#   ADMIN_PASS=your_strong_admin_password
#   SECRET_KEY=a_long_random_secret
#   PORT=8080
#
# Optional:
#   DB_PATH=modpanel.db
#   SESSION_HOURS=12
#   API_SESSION_MINUTES=30
#
# IMPORTANT:
# - The server is the source of truth for keys/expiry/permissions.
# - Never put ADMIN_PASS or SECRET_KEY inside the APK.
# - HTTPS should be provided by your host (Railway, Render, etc.).
# ============================================================

app = Flask(__name__)

ADMIN_USER = os.environ.get("ADMIN_USER", "").strip()
ADMIN_PASS = os.environ.get("ADMIN_PASS", "")
SECRET_KEY = os.environ.get("SECRET_KEY", "")

if not ADMIN_USER or not ADMIN_PASS or not SECRET_KEY:
    raise RuntimeError(
        "Set ADMIN_USER, ADMIN_PASS and SECRET_KEY environment variables "
        "before starting the server."
    )

app.secret_key = SECRET_KEY
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SECURE=True,       # HTTPS in production
    SESSION_COOKIE_SAMESITE="Lax",
    PERMANENT_SESSION_LIFETIME=timedelta(
        hours=int(os.environ.get("SESSION_HOURS", "12"))
    ),
)

DB_PATH = os.environ.get("DB_PATH", "modpanel.db")
API_SESSION_MINUTES = int(os.environ.get("API_SESSION_MINUTES", "30"))

# Small in-memory rate limiter. For a single private server this is useful.
# If you later run multiple workers/instances, move rate limiting to Redis.
RATE_BUCKETS = {}


# ============================================================
# TIME / JSON HELPERS
# ============================================================

def now_utc():
    return datetime.now(timezone.utc)


def iso(dt):
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_iso(value):
    if not value:
        return None
    try:
        value = value.strip()
        if value.endswith("Z"):
            value = value[:-1] + "+00:00"
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def db():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    with db() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS settings (
            name TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS keys (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            key TEXT NOT NULL UNIQUE,
            active INTEGER NOT NULL DEFAULT 1,
            device_limit INTEGER NOT NULL DEFAULT 1,
            expires_at TEXT,
            created_at TEXT NOT NULL,
            note TEXT DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS devices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            key_id INTEGER NOT NULL,
            hwid TEXT NOT NULL,
            first_seen TEXT NOT NULL,
            last_seen TEXT NOT NULL,
            label TEXT DEFAULT '',
            UNIQUE(key_id, hwid),
            FOREIGN KEY(key_id) REFERENCES keys(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS api_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            token_hash TEXT NOT NULL UNIQUE,
            key_id INTEGER NOT NULL,
            hwid TEXT NOT NULL,
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            last_seen TEXT NOT NULL,
            revoked INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY(key_id) REFERENCES keys(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS audit_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            event TEXT NOT NULL,
            ip TEXT DEFAULT '',
            details TEXT DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS feature_permissions (
            feature_id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 1,
            min_client_version TEXT DEFAULT '1.0.0'
        );

        CREATE TABLE IF NOT EXISTS key_permissions (
            key_id INTEGER NOT NULL,
            feature_id INTEGER NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 1,
            PRIMARY KEY(key_id, feature_id),
            FOREIGN KEY(key_id) REFERENCES keys(id) ON DELETE CASCADE,
            FOREIGN KEY(feature_id) REFERENCES feature_permissions(feature_id) ON DELETE CASCADE
        );
        """)

        defaults = {
            "menu_enabled": "1",
            "maintenance": "0",
            "menu_version": "1.0.0",
            "min_client_version": "1.0.0",
            "announcement": "",
        }

        for k, v in defaults.items():
            c.execute(
                "INSERT OR IGNORE INTO settings(name,value) VALUES(?,?)",
                (k, v)
            )

        # These IDs come from the supplied Main.cpp GetFeatureList/Changes.
        # The server only controls whether a feature is allowed.
        features = [
            (98, "BYPASS V.5.0"),
            (15000, "FPS UNLOCKER"),
            (1100, "Kezza Effect"),
            (1107, "Dog"),
            (88, "PING AVERAGE"),
            (106, "Zoom"),
            (66, "MULTI HIT"),
            (105, "Unlimited Gauge Skill"),
            (295, "ATTACK THROUGH WALL"),
            (1002, "NO FLAG ANIM"),
            (100, "CANCEL EFFECT"),
            (3, "NO FALL"),
            (1105, "FastCapture"),
            (458, "FlagRange"),
            (459, "No Status Effect"),
            (30, "BYPASS WIND"),
            (29, "NORMAL AURA"),
            (47, "UNLIMITED AURA"),
            (120, "Online Long Range"),
            (4, "SPEED PLAYER"),
            (90, "FOV ME"),
            (110, "SPEED GAME"),
            (456, "PLAYER DASH"),
            (455, "PlayerScale"),
            (140, "20X LONG RANGE"),
            (77, "STOP BOT"),
            (8901, "Normal Auto Kill"),
            (8902, "Super Auto Kill"),
            (231, "Unlimited Fall"),
            (25, "INVICIBLE"),
            (33, "SKILL NO CD"),
            (20, "DISABLE JUMP"),
        ]

        for fid, name in features:
            c.execute("""
                INSERT OR IGNORE INTO feature_permissions
                (feature_id,name,enabled,min_client_version)
                VALUES(?,?,1,'1.0.0')
            """, (fid, name))


def audit(event, details="", ip=None):
    try:
        with db() as c:
            c.execute(
                "INSERT INTO audit_logs(created_at,event,ip,details) VALUES(?,?,?,?)",
                (iso(now_utc()), event, ip or request.remote_addr or "", details[:2000])
            )
    except Exception:
        pass


def get_setting(name, default=None):
    with db() as c:
        row = c.execute(
            "SELECT value FROM settings WHERE name=?",
            (name,)
        ).fetchone()
    return row["value"] if row else default


def set_setting(name, value):
    with db() as c:
        c.execute("""
            INSERT INTO settings(name,value) VALUES(?,?)
            ON CONFLICT(name) DO UPDATE SET value=excluded.value
        """, (name, str(value)))


# ============================================================
# RATE LIMITING
# ============================================================

def rate_limit(bucket, limit, window_seconds):
    now = time.time()
    ip = request.remote_addr or "unknown"
    key = f"{bucket}:{ip}"

    values = RATE_BUCKETS.get(key, [])
    values = [x for x in values if now - x < window_seconds]

    if len(values) >= limit:
        RATE_BUCKETS[key] = values
        return False

    values.append(now)
    RATE_BUCKETS[key] = values

    # Prevent this tiny dict from growing forever.
    if len(RATE_BUCKETS) > 5000:
        cutoff = now - 3600
        for k in list(RATE_BUCKETS):
            RATE_BUCKETS[k] = [x for x in RATE_BUCKETS[k] if x >= cutoff]
            if not RATE_BUCKETS[k]:
                RATE_BUCKETS.pop(k, None)

    return True


# ============================================================
# AUTH / CSRF
# ============================================================

def csrf_token():
    """Return a stable CSRF token for the current Flask session."""
    token = session.get("_csrf")

    # Only create a token when the session does not already contain a
    # valid token. This prevents the token from changing between pages.
    if not isinstance(token, str) or len(token) < 32:
        token = secrets.token_urlsafe(32)
        session["_csrf"] = token
        session.modified = True

    return token


def rotate_csrf():
    """Rotate CSRF after an authentication state change."""
    token = secrets.token_urlsafe(32)
    session["_csrf"] = token
    session.modified = True
    return token


def require_csrf():
    """Validate the CSRF token from a form or X-CSRF-Token header."""
    submitted = request.form.get("csrf")
    if not submitted:
        submitted = request.headers.get("X-CSRF-Token")

    stored = session.get("_csrf", "")

    if (
        not isinstance(submitted, str)
        or not isinstance(stored, str)
        or not submitted
        or not stored
        or len(submitted) > 512
        or len(stored) > 512
        or not hmac.compare_digest(submitted, stored)
    ):
        abort(400, "Invalid CSRF token")


def admin_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not session.get("admin"):
            return redirect(url_for("login"))
        return fn(*args, **kwargs)
    return wrapper


def hash_token(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def current_client_ip():
    return request.headers.get("X-Forwarded-For", request.remote_addr or "")


# ============================================================
# CSRF SESSION INITIALIZATION
# ============================================================

@app.before_request
def ensure_csrf_session():
    # Create the CSRF token only when missing/invalid. Do not rotate it
    # on every request, otherwise forms opened in another page can fail.
    csrf_token()


# ============================================================
# KEY HELPERS
# ============================================================

def generate_key():
    return "AK-" + "-".join(
        "".join(secrets.choice("ABCDEFGHJKLMNPQRSTUVWXYZ23456789") for _ in range(4))
        for _ in range(3)
    )


def normalize_key(value):
    return (value or "").strip().upper()


def key_status(row):
    if not row:
        return "NOT_FOUND"
    if not row["active"]:
        return "DISABLED"
    exp = parse_iso(row["expires_at"])
    if exp and exp <= now_utc():
        return "EXPIRED"
    return "ACTIVE"


def valid_key_for_device(key_value, hwid, create_device=True):
    key_value = normalize_key(key_value)
    hwid = (hwid or "").strip()

    if not key_value or not hwid:
        return None, None, "Missing key or device id"

    with db() as c:
        key_row = c.execute(
            "SELECT * FROM keys WHERE key=?",
            (key_value,)
        ).fetchone()

        if not key_row:
            return None, None, "Invalid key"

        status = key_status(key_row)
        if status == "DISABLED":
            return None, None, "Key disabled"
        if status == "EXPIRED":
            return None, None, "Key expired"

        device = c.execute("""
            SELECT * FROM devices
            WHERE key_id=? AND hwid=?
        """, (key_row["id"], hwid)).fetchone()

        device_count = c.execute("""
            SELECT COUNT(*) AS n FROM devices WHERE key_id=?
        """, (key_row["id"],)).fetchone()["n"]

        if not device and create_device:
            if device_count >= key_row["device_limit"]:
                return None, None, "Device limit reached"

            t = iso(now_utc())
            c.execute("""
                INSERT INTO devices(key_id,hwid,first_seen,last_seen)
                VALUES(?,?,?,?)
            """, (key_row["id"], hwid, t, t))
        elif device:
            c.execute("""
                UPDATE devices SET last_seen=? WHERE id=?
            """, (iso(now_utc()), device["id"]))

        return key_row, device, None


def feature_map_for_key(key_id):
    with db() as c:
        rows = c.execute("""
            SELECT
                f.feature_id,
                f.name,
                f.enabled AS global_enabled,
                f.min_client_version,
                COALESCE(kp.enabled, 1) AS key_enabled
            FROM feature_permissions f
            LEFT JOIN key_permissions kp
              ON kp.feature_id=f.feature_id AND kp.key_id=?
            ORDER BY f.feature_id
        """, (key_id,)).fetchall()

    return {
        str(r["feature_id"]): {
            "name": r["name"],
            "enabled": bool(r["global_enabled"] and r["key_enabled"]),
            "min_client_version": r["min_client_version"],
        }
        for r in rows
    }


def menu_payload(key_row):
    return {
        "menu_enabled": get_setting("menu_enabled", "1") == "1",
        "maintenance": get_setting("maintenance", "0") == "1",
        "menu_version": get_setting("menu_version", "1.0.0"),
        "min_client_version": get_setting("min_client_version", "1.0.0"),
        "announcement": get_setting("announcement", ""),
        "server_time": iso(now_utc()),
        "expires_at": key_row["expires_at"],
        "permissions": feature_map_for_key(key_row["id"]),
    }


# ============================================================
# API
# ============================================================

@app.get("/api/v1/health")
def api_health():
    return jsonify({
        "success": True,
        "server": "online",
        "server_time": iso(now_utc())
    })


@app.post("/api/v1/auth/login")
def api_login():
    if not rate_limit("api_login", 12, 60):
        return jsonify({
            "success": False,
            "message": "Too many attempts"
        }), 429

    data = request.get_json(silent=True) or request.form
    key_value = normalize_key(data.get("key", ""))
    hwid = str(data.get("hwid", "")).strip()
    client_version = str(data.get("client_version", "1.0.0")).strip()

    if len(hwid) > 256:
        return jsonify({"success": False, "message": "Invalid device id"}), 400

    key_row, device, error = valid_key_for_device(key_value, hwid, True)

    if error:
        audit(
            "API_LOGIN_FAILED",
            f"key={key_value[:8]}... hwid={hwid[:16]}... client={client_version} reason={error}",
            current_client_ip()
        )
        return jsonify({
            "success": False,
            "message": error
        }), 403

    menu = menu_payload(key_row)

    if not menu["menu_enabled"]:
        return jsonify({
            "success": False,
            "message": "MOD MENU disabled by server",
            "server_time": menu["server_time"]
        }), 403

    if menu["maintenance"]:
        return jsonify({
            "success": False,
            "message": "Server maintenance",
            "server_time": menu["server_time"]
        }), 503

    # We intentionally do not trust the client version for security.
    # It is only used to tell the client if an update is required.
    token = secrets.token_urlsafe(48)
    token_hash = hash_token(token)
    created = now_utc()
    expires = created + timedelta(minutes=API_SESSION_MINUTES)

    with db() as c:
        c.execute("""
            INSERT INTO api_sessions
            (token_hash,key_id,hwid,created_at,expires_at,last_seen)
            VALUES(?,?,?,?,?,?)
        """, (
            token_hash,
            key_row["id"],
            hwid,
            iso(created),
            iso(expires),
            iso(created),
        ))

    audit(
        "API_LOGIN_OK",
        f"key={key_value[:8]}... hwid={hwid[:16]}... client={client_version}",
        current_client_ip()
    )

    return jsonify({
        "success": True,
        "session_token": token,
        "session_expires_at": iso(expires),
        "key_expires_at": key_row["expires_at"],
        "server_time": menu["server_time"],
        "menu_version": menu["menu_version"],
        "min_client_version": menu["min_client_version"],
        "permissions": menu["permissions"],
        "announcement": menu["announcement"],
    })


def require_api_session():
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None, "Missing authorization"

    token = auth[7:].strip()
    if len(token) < 20 or len(token) > 512:
        return None, "Invalid session"

    token_hash = hash_token(token)

    with db() as c:
        row = c.execute("""
            SELECT
                s.*,
                k.key,
                k.active,
                k.device_limit,
                k.expires_at AS key_expires_at
            FROM api_sessions s
            JOIN keys k ON k.id=s.key_id
            WHERE s.token_hash=? AND s.revoked=0
        """, (token_hash,)).fetchone()

        if not row:
            return None, "Invalid session"

        session_exp = parse_iso(row["expires_at"])
        key_exp = parse_iso(row["key_expires_at"])

        if not session_exp or session_exp <= now_utc():
            c.execute(
                "UPDATE api_sessions SET revoked=1 WHERE id=?",
                (row["id"],)
            )
            return None, "Session expired"

        if not row["active"]:
            return None, "Key disabled"

        if key_exp and key_exp <= now_utc():
            return None, "Key expired"

        # The device must still be registered for the key.
        device = c.execute("""
            SELECT id FROM devices
            WHERE key_id=? AND hwid=?
        """, (row["key_id"], row["hwid"])).fetchone()

        if not device:
            return None, "Device not registered"

        c.execute("""
            UPDATE api_sessions SET last_seen=? WHERE id=?
        """, (iso(now_utc()), row["id"]))

        return row, None


@app.get("/api/v1/session")
def api_session():
    row, error = require_api_session()
    if error:
        return jsonify({"success": False, "message": error}), 401

    with db() as c:
        key_row = c.execute(
            "SELECT * FROM keys WHERE id=?",
            (row["key_id"],)
        ).fetchone()

    menu = menu_payload(key_row)

    if not menu["menu_enabled"] or menu["maintenance"]:
        return jsonify({
            "success": False,
            "message": "MOD MENU unavailable",
            "server_time": menu["server_time"]
        }), 403

    return jsonify({
        "success": True,
        "server_time": menu["server_time"],
        "session_expires_at": row["expires_at"],
        "key_expires_at": key_row["expires_at"],
        "permissions": menu["permissions"],
        "menu_version": menu["menu_version"],
        "min_client_version": menu["min_client_version"],
        "announcement": menu["announcement"],
    })


@app.post("/api/v1/session/logout")
def api_logout():
    row, error = require_api_session()
    if error:
        return jsonify({"success": False, "message": error}), 401

    auth = request.headers.get("Authorization", "")
    token_hash = hash_token(auth[7:].strip())

    with db() as c:
        c.execute(
            "UPDATE api_sessions SET revoked=1 WHERE token_hash=?",
            (token_hash,)
        )

    return jsonify({"success": True, "message": "Logged out"})


# ============================================================
# ADMIN WEB PANEL
# ============================================================

BASE_STYLE = """
<style>
:root{--bg:#070b12;--panel:#101927;--line:#26364d;--text:#eef5ff;--muted:#8fa2bb;--blue:#3b82f6;--green:#34d399;--red:#fb7185}
*{box-sizing:border-box}html{scroll-behavior:smooth}
body{margin:0;background:radial-gradient(circle at 15% 0%,#10223b 0,#070b12 40%),var(--bg);color:var(--text);font-family:Inter,system-ui,-apple-system,"Segoe UI",Arial,sans-serif;min-height:100vh}
a{color:#9cc8ff;text-decoration:none}a:hover{color:#fff}
.wrap{max-width:1320px;margin:0 auto;padding:24px 16px 60px}
.topbar{display:flex;align-items:center;justify-content:space-between;gap:16px;margin-bottom:18px;padding:14px 16px;background:rgba(16,25,39,.9);backdrop-filter:blur(16px);border:1px solid var(--line);border-radius:18px;position:sticky;top:12px;z-index:20;box-shadow:0 18px 55px rgba(0,0,0,.3)}
.brand{display:flex;align-items:center;gap:11px;font-weight:800}.brand-dot{width:11px;height:11px;border-radius:50%;background:var(--green);box-shadow:0 0 18px rgba(52,211,153,.75)}
.nav{display:flex;gap:7px;flex-wrap:wrap;justify-content:flex-end}.nav a,.btn{display:inline-flex;align-items:center;justify-content:center;padding:9px 12px;border-radius:10px;background:#172337;color:#eaf2ff;border:1px solid #2c3e57;cursor:pointer;font-weight:700;transition:.18s}.nav a:hover,.btn:hover{transform:translateY(-1px);border-color:#4d6b94;background:#1c2c44}
.btn.primary{background:linear-gradient(135deg,#2563eb,#3b82f6);border-color:#4c8dff}.btn.danger{background:#65152b;border-color:#a83b59}
.hero{overflow:hidden;position:relative;border:1px solid var(--line);border-radius:20px;background:#0c1421;margin-bottom:18px;box-shadow:0 18px 55px rgba(0,0,0,.28)}.hero img{display:block;width:100%;height:auto;max-height:270px;object-fit:cover;opacity:.78}.hero-overlay{position:absolute;inset:0;padding:28px;display:flex;align-items:flex-end;background:linear-gradient(90deg,rgba(5,9,15,.92),rgba(5,9,15,.25) 70%,rgba(5,9,15,.65))}.hero h1{font-size:clamp(26px,4vw,44px);margin:0 0 8px}.hero p{margin:0;color:#b8c7da}
.card{background:linear-gradient(180deg,rgba(18,29,45,.97),rgba(12,20,32,.97));border:1px solid var(--line);border-radius:17px;padding:18px;margin-bottom:15px;box-shadow:0 12px 35px rgba(0,0,0,.18)}
h1,h2,h3{margin-top:0}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:13px}.stat{font-size:30px;font-weight:850;margin-top:5px}.small{color:var(--muted);font-size:13px}.row{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:10px}
input,select,textarea{width:100%;padding:11px 12px;margin:6px 0 12px;background:#09111d;color:#fff;border:1px solid #30435d;border-radius:10px;outline:none}input:focus,select:focus,textarea:focus{border-color:#5b9cff;box-shadow:0 0 0 3px rgba(59,130,246,.15)}
table{width:100%;border-collapse:separate;border-spacing:0}th,td{padding:11px;border-bottom:1px solid #243247;text-align:left;vertical-align:middle}th{color:#a9bad0;font-size:12px;text-transform:uppercase;letter-spacing:.05em}tr:hover td{background:rgba(59,130,246,.035)}
.badge{display:inline-flex;padding:4px 9px;border-radius:999px;background:#1c2a3d;border:1px solid #30435d}.ok{color:var(--green)}.bad{color:var(--red)}.inline{display:inline}code{color:#b9d7ff;background:#09111d;padding:3px 6px;border-radius:6px}.login-shell{min-height:92vh;display:grid;place-items:center}.login-card{width:min(460px,100%);padding:26px}.footer{text-align:center;color:#71839a;font-size:12px;margin-top:20px}
@media(max-width:800px){.topbar{position:static;align-items:flex-start;flex-direction:column}.nav{justify-content:flex-start}.hero-overlay{padding:20px}table{display:block;overflow-x:auto;white-space:nowrap}}
.anime-gif{width:100%;max-width:420px;height:220px;object-fit:cover;display:block;margin:0 auto 18px;border-radius:18px;border:1px solid rgba(255,255,255,.12);box-shadow:0 12px 35px rgba(0,0,0,.35)}
</style>
"""


def page(title, body):
    return render_template_string(
        BASE_STYLE + """
        <div class="wrap">
          <header class="topbar">
            <div class="brand"><span class="brand-dot"></span>Private Control Panel</div>
            <nav class="nav">
              <a href="{{ url_for('dashboard') }}">Dashboard</a>
              <a href="{{ url_for('keys_page') }}">Keys</a>
              <a href="{{ url_for('devices_page') }}">Devices</a>
              <a href="{{ url_for('features_page') }}">Permissions</a>
              <a href="{{ url_for('settings_page') }}">Settings</a>
              <a href="{{ url_for('logs_page') }}">Logs</a>
            <a href="{{ url_for('login_activity_page') }}">Login Activity</a>
              <a href="{{ url_for('logout') }}">Logout</a>
            </nav>
          </header>
          """ + body + """
          <div class="footer">Secure admin panel • Server time: UTC</div>
        </div>
        """,
        title=title,
        csrf=csrf_token()
    )

@app.get("/login")
def login():
    if session.get("admin"):
        return redirect(url_for("dashboard"))

    return render_template_string(BASE_STYLE + """
    <div class="wrap login-shell">
      <div class="card login-card">
        <div class="hero" style="margin:-26px -26px 22px;border-radius:17px 17px 0 0;border:0">
          <img class="anime-gif" src="https://media.tenor.com/images/7f3d3c7c7e5f4f7d8e7f7c6c4a0b0d4b/tenor.gif" alt="Anime GIF">
          <div class="hero-overlay"><div><h1>Private Control Panel</h1><p>Administrator access</p></div></div>
        </div>
        <p class="small">Administrator login</p>
        {% if error %}<p class="bad">{{ error }}</p>{% endif %}
        <form method="post">
          <input type="hidden" name="csrf" value="{{ csrf }}">
          <label>Username</label>
          <input name="username" autocomplete="username" required>
          <label>Password</label>
          <input type="password" name="password"
                 autocomplete="current-password" required>
          <button class="btn primary" type="submit">Login</button>
        </form>
      </div>
    </div>
    """, error=None, csrf=csrf_token())


@app.post("/login")
def login_post():
    if not rate_limit("admin_login", 8, 60):
        return render_template_string(
            BASE_STYLE + "<div class='wrap'><div class='card'><p class='bad'>Too many login attempts. Try again later.</p></div></div>"
        ), 429

    require_csrf()

    username = request.form.get("username", "")
    password = request.form.get("password", "")

    if hmac.compare_digest(username, ADMIN_USER) and hmac.compare_digest(password, ADMIN_PASS):
        session.clear()
        session.permanent = True
        session["admin"] = True
        rotate_csrf()
        audit("ADMIN_LOGIN", "success", current_client_ip())
        return redirect(url_for("dashboard"))

    audit("ADMIN_LOGIN_FAILED", "invalid credentials", current_client_ip())
    return render_template_string(
        BASE_STYLE + """
        <div class="wrap" style="max-width:450px">
          <div class="card">
            <h1>Private Control Panel</h1>
            <p class="bad">Invalid login</p>
            <form method="post">
              <input type="hidden" name="csrf" value="{{ csrf }}">
              <input name="username" required>
              <input type="password" name="password" required>
              <button class="btn primary">Login</button>
            </form>
          </div>
        </div>
        """,
        csrf=csrf_token()
    ), 401


@app.get("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.get("/")
@admin_required
def dashboard():
    with db() as c:
        total = c.execute("SELECT COUNT(*) n FROM keys").fetchone()["n"]
        active = c.execute(
            "SELECT COUNT(*) n FROM keys WHERE active=1"
        ).fetchone()["n"]
        devices = c.execute("SELECT COUNT(*) n FROM devices").fetchone()["n"]
        sessions = c.execute("""
            SELECT COUNT(*) n FROM api_sessions
            WHERE revoked=0 AND expires_at>?
        """, (iso(now_utc()),)).fetchone()["n"]

    body = """
    <div class="hero">
      <img src="data:image/gif;base64,R0lGODlhhAMEAYcAAOfv/OTs+uPr+eHp997m9dvk89vj8tbf79Xe7c3X58jT5MXQ4cLO38HM3sDL3b7K3LzI2rvH2brG2LjE17fE1rbD1rbC1bXB1LTB1LK/0rG+0rC90a+90K+80Kq4zKi2yqWzyFXcoFPXnlPWnaOyx1HSm1DQmk/MmU7KmEzHlkm9kki6kUe6kUa3j0a2j0W0jkWzjkSyjUSxjZ6tw5mpv5KjuYakx4WjxoSixY6ftoOhxIKgw4GfwoCewoCewYydtImasoeYsIWXr3+dwH6cv32bvnyavXuZvHqYvHqYu3mXu4OVrXeVuXeVuHaUt3SStXORtXORtHKQtHGPs3GPsniLpHWIonOHoXGFn0OwjEOvjEKti0Kri0CmiD+miD+khz6jh3COsnCOsW+NsG2Lrj6ihmuJrTSKe2qIrGqIq2mHq2mHqmiGqmiGqWeFqWeFqGaEp2WDp2WDpmV6lmR5lGOBpGF/o2B+ol99oF17n117nlx6nlx6nVt5nTB/dy57dWN4lFp4nDB43C14dGJ3k1l3m1h2mlxyjlZ0mFlvjFdui1ZsilVsiU9tkU9tkE5skE5qjUxnikxkgktjgUtigUpigElhf0hgfytzcSpwcClvb0dlikZkiCVma0RihkNhhUJghCRiakdffkZefUVefUFfhERdfEBegz9dgSJeaCJdZ0JbekFaeUBZeT5cgDtZfT5XdzpYfDlXezhWejdVeTlTczVTdzZQcTJQdTJQdDJMbSJcZyFaZiFZZiBYZR9VZB9UYzBOci9Nci5McSxKbyxKbh1QYhtMYCtJbSlHaytGaChGaydFaiZEaCdDZyRCZiNBZiRAYiJAZSE/ZCA+Yx89Yh89YRY9WR88YB07YB07Xx05XRw6Xxw5XRo4XRk3XBk2WhM4Vxg2Wxg1WRc1Whc1WRc0WBY0WRY0WBI0VRYzVxUzWBUyVxQyVxQyVhEzVREyVBMxVhMxVRIwVRIvVBAwVBEvVBEvUxAuUw8uUg8tUgcLEgAAAAAAAAAAAAAAACH/C05FVFNDQVBFMi4wAwEAAAAh+QQACgAAACwAAAAAhAMEAQAI/wD3CRxIsKDBgwgTKlzIsKHDhxAjSpxIsaLFixgzatzIsaPHjyBDihxJsqTJkyhTqlzJsqXLlzBjypxJs6bNmzhz6tzJs6fPn0CDCh1KtKjRo0iTKl3KtKnTp1CjSp1KtarVq1izat3KtavXr2DDih1LtqzZs2jTql3Ltq3bt3Djyp1Lt67du3jz6t3Lt6/fv4ADCx5MuLDhw4gTK17MuLHjx5AjS55MubLly5gza97MubPnz6BDix5NurTp06hTq17NurXr17Bjy549WJDt27hz697Nu7fv38CDCx9OvLjx48iTK1/OvLnz59CjS59Ovbr169iza9++vSn37+DDi/8fT768+fPo06tfz769+99HdeubT7++/fv6guLfzz8/0P4A1qdfgAEOSGB/Bh64X4IK3sdggwL+ByF+D94nn4MOFdgRfblNqGBMHoY4H4giekhiiRCeiOKHMK2YYosusvhSjDK6ROOBKnrY4Y30YYQfbjwSWFKQKA5JpIhGHmkiSUqGmGSTDT4JJY5MThlllVZSOdKRQPI4EYC3ZQngRmKuSGaZJZ6JppMarYlkm24umVGccvpI54tz3nllnlaGeeNDB/qpp31fDpqmRIYeGlGiby7KaJ2APjphoZLuCVGleF6KaY2Rrimoiws1+OmmoW6qKEKmnnpQqo2iyiqkBr3/CmtBsk6qUK2Zroorp7EaOqqZBE34a6q07srmQMYeK1CykDJrK7LOWrpPtNJSy6m1Wi4r6bBucovtt+CGK+645JZr7rnYelumbei26+678MYr77z0fsuunvfWq+++/Pbr778A15tvt4IEbPDBCCes8MIMEznwug83LPHEFFds8cXiqqtkxBh37PHHIIcs8o0cQ1nyyCinrPLKLDN8MpcFtyzzzDTXbPO4LweZ88089+zzz0BvHHOfQwdt9NFIJ620fTvHqPHSUEct9dQVP+1i01RnrfXWXMOLNYpfdy322GSXzWrYIqJt9tpst+02zFOq/fbcdNdt935yi1r03Xz3/+0333krGPjfhBduONCDE5j44Yw37rjIi4O59+OUV245ypH3l/nlnHfu+bub4z3556SXbvq8of84+umst+56uqvTmPrrtNdue5OzMx377bz37nuWudcX/O/EF1/88PMhb/zyzLuuvPLNRy8958/vTqAAF2AwASz6YG9BA6ToQ4A+QdBBHwe16DMHANHow8gFFwAAvyLYw48FfXM0YIEHzswHiAMRcAAh5hMADNBnfO+L3/y8Bz59/C+AAxQffrCnPe6pj30ENOB8xtc9+F3gfhycnghH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" alt="Control Panel">
      <div class="hero-overlay"><div><h1>Private Control Panel</h1><p>Secure key, device and session management</p></div></div>
    </div>
    <div class="grid">
      <div class="card"><div class="small">Keys</div><div class="stat">{{ total }}</div></div>
      <div class="card"><div class="small">Active Keys</div><div class="stat">{{ active }}</div></div>
      <div class="card"><div class="small">Registered Devices</div><div class="stat">{{ devices }}</div></div>
      <div class="card"><div class="small">Live API Sessions</div><div class="stat">{{ sessions }}</div></div>
    </div>

    <div class="card">
      <h2>MOD MENU Status</h2>
      <p>
        Enabled:
        <b class="{{ 'ok' if menu_enabled else 'bad' }}">
          {{ 'YES' if menu_enabled else 'NO' }}
        </b>
      </p>
      <p>Version: <b>{{ version }}</b></p>
      <p>Minimum Client: <b>{{ minver }}</b></p>
      <p>Maintenance: <b>{{ 'ON' if maintenance else 'OFF' }}</b></p>
    </div>
    """
    return page("Dashboard", render_template_string(
        body,
        total=total, active=active, devices=devices, sessions=sessions,
        menu_enabled=get_setting("menu_enabled") == "1",
        version=get_setting("menu_version"),
        minver=get_setting("min_client_version"),
        maintenance=get_setting("maintenance") == "1"
    ))


@app.get("/keys")
@admin_required
def keys_page():
    created_key = session.pop("created_key", None)
    with db() as c:
        rows = c.execute("""
            SELECT
              k.*,
              (SELECT COUNT(*) FROM devices d WHERE d.key_id=k.id) AS device_count
            FROM keys k
            ORDER BY k.id DESC
        """).fetchall()

    body = """
    <h1>Keys</h1>

    {% if created_key %}
    <div class="card created-key-card">
      <div class="small">NEW ACTIVATION KEY</div>
      <div class="created-key-row">
        <code id="newKey">{{ created_key }}</code>
        <button type="button" class="btn primary" onclick="copyNewKey()">نسخ</button>
      </div>
      <div id="copyStatus" class="small">The new key is ready to copy.</div>
    </div>
    {% endif %}

    <div class="card">
      <h2>Create Key</h2>
      <form method="post" action="{{ url_for('create_key') }}">
        <input type="hidden" name="csrf" value="{{ csrf }}">
        <div class="row">
          <div>
            <label>Custom key (optional)</label>
            <input name="key" placeholder="AK-XXXX-XXXX-XXXX">
          </div>
          <div>
            <label>Device limit</label>
            <input type="number" name="device_limit" min="1" max="100" value="1">
          </div>
          <div>
            <label>Days (0 = permanent)</label>
            <input type="number" name="days" min="0" value="30">
          </div>
        </div>
        <input name="note" placeholder="Note">
        <button class="btn primary">Create</button>
      </form>
    </div>

    <div class="card">
      <table>
        <tr>
          <th>Key</th><th>Status</th><th>Devices</th>
          <th>Expiration</th><th>Actions</th>
        </tr>
        {% for r in rows %}
        <tr>
          <td><code>{{ r['key'] }}</code><br><span class="small">{{ r['note'] }}</span></td>
          <td>{{ status(r) }}</td>
          <td>{{ r['device_count'] }}/{{ r['device_limit'] }}</td>
          <td>
            {% if r['expires_at'] %}
              <span class="countdown" data-exp="{{ r['expires_at'] }}">calculating...</span>
              <br><span class="small">{{ r['expires_at'] }}</span>
            {% else %}Permanent{% endif %}
          </td>
          <td>
            <form class="inline" method="post" action="{{ url_for('toggle_key', key_id=r['id']) }}">
              <input type="hidden" name="csrf" value="{{ csrf }}">
              <button class="btn">{{ 'Disable' if r['active'] else 'Enable' }}</button>
            </form>
            <form class="inline" method="post" action="{{ url_for('delete_key', key_id=r['id']) }}"
                  onsubmit="return confirm('Delete this key and its devices/sessions?')">
              <input type="hidden" name="csrf" value="{{ csrf }}">
              <button class="btn danger">Delete</button>
            </form>
          </td>
        </tr>
        {% endfor %}
      </table>
    </div>

    <script>
    async function copyNewKey(){
        const el = document.getElementById("newKey");
        if (!el) return;
        const value = el.textContent.trim();
        try {
            await navigator.clipboard.writeText(value);
        } catch (e) {
            const area = document.createElement("textarea");
            area.value = value;
            area.style.position = "fixed";
            area.style.opacity = "0";
            document.body.appendChild(area);
            area.focus();
            area.select();
            document.execCommand("copy");
            area.remove();
        }
        const btn = document.querySelector('button[onclick="copyNewKey()"]');
        if (btn) {
            const old = btn.textContent;
            btn.textContent = "تم النسخ ✓";
            setTimeout(() => btn.textContent = old, 1400);
        }
    }

    function countdown(){
      document.querySelectorAll('[data-exp]').forEach(function(el){
        const end = new Date(el.dataset.exp).getTime();
        let sec = Math.max(0, Math.floor((end-Date.now())/1000));
        const d=Math.floor(sec/86400); sec%=86400;
        const h=Math.floor(sec/3600); sec%=3600;
        const m=Math.floor(sec/60); const s=sec%60;
        el.textContent = d+'d '+h+'h '+m+'m '+s+'s';
      });
    }
    countdown(); setInterval(countdown,1000);
    </script>
    """
    return page("Keys", render_template_string(
        body, rows=rows, status=key_status, csrf=csrf_token()
    ))


@app.post("/keys/create")
@admin_required
def create_key():
    require_csrf()

    value = normalize_key(request.form.get("key"))
    if not value:
        value = generate_key()

    try:
        device_limit = max(1, min(100, int(request.form.get("device_limit", "1"))))
        days = max(0, int(request.form.get("days", "30")))
    except (TypeError, ValueError):
        return "Invalid device limit or days. <a href='/keys'>Back</a>", 400
    note = request.form.get("note", "")[:500]

    expires = None if days == 0 else iso(now_utc() + timedelta(days=days))

    try:
        with db() as c:
            c.execute("""
                INSERT INTO keys(key,active,device_limit,expires_at,created_at,note)
                VALUES(?,?,?,?,?,?)
            """, (
                value, 1, device_limit, expires, iso(now_utc()), note
            ))
    except sqlite3.IntegrityError:
        return "Key already exists. <a href='/keys'>Back</a>", 409

    session["created_key"] = value
    session.modified = True
    audit("KEY_CREATED", f"key={value[:8]}...")
    return redirect(url_for("keys_page"))


@app.post("/keys/<int:key_id>/toggle")
@admin_required
def toggle_key(key_id):
    require_csrf()
    with db() as c:
        row = c.execute(
            "SELECT active,key FROM keys WHERE id=?",
            (key_id,)
        ).fetchone()
        if not row:
            abort(404)
        new_value = 0 if row["active"] else 1
        c.execute(
            "UPDATE keys SET active=? WHERE id=?",
            (new_value, key_id)
        )
        if not new_value:
            c.execute(
                "UPDATE api_sessions SET revoked=1 WHERE key_id=?",
                (key_id,)
            )

    audit("KEY_TOGGLED", f"key_id={key_id} active={new_value}")
    return redirect(url_for("keys_page"))


@app.post("/keys/<int:key_id>/delete")
@admin_required
def delete_key(key_id):
    require_csrf()
    with db() as c:
        c.execute("DELETE FROM keys WHERE id=?", (key_id,))
    audit("KEY_DELETED", f"key_id={key_id}")
    return redirect(url_for("keys_page"))


@app.get("/devices")
@admin_required
def devices_page():
    with db() as c:
        rows = c.execute("""
            SELECT d.*, k.key
            FROM devices d
            JOIN keys k ON k.id=d.key_id
            ORDER BY d.last_seen DESC
        """).fetchall()

    body = """
    <h1>Devices</h1>
    <div class="card">
      <table>
        <tr><th>Key</th><th>HWID</th><th>First Seen</th><th>Last Seen</th><th>Action</th></tr>
        {% for r in rows %}
        <tr>
          <td>{{ r['key'] }}</td>
          <td><code>{{ r['hwid'] }}</code></td>
          <td>{{ r['first_seen'] }}</td>
          <td>{{ r['last_seen'] }}</td>
          <td>
            <form method="post" action="{{ url_for('delete_device', device_id=r['id']) }}">
              <input type="hidden" name="csrf" value="{{ csrf }}">
              <button class="btn danger">Remove</button>
            </form>
          </td>
        </tr>
        {% endfor %}
      </table>
    </div>
    """
    return page("Devices", render_template_string(body, rows=rows, csrf=csrf_token()))


@app.post("/devices/<int:device_id>/delete")
@admin_required
def delete_device(device_id):
    require_csrf()
    with db() as c:
        row = c.execute(
            "SELECT key_id,hwid FROM devices WHERE id=?",
            (device_id,)
        ).fetchone()
        if row:
            c.execute("DELETE FROM devices WHERE id=?", (device_id,))
            c.execute("""
                UPDATE api_sessions SET revoked=1
                WHERE key_id=? AND hwid=?
            """, (row["key_id"], row["hwid"]))
    audit("DEVICE_REMOVED", f"device_id={device_id}")
    return redirect(url_for("devices_page"))


@app.get("/features")
@admin_required
def features_page():
    with db() as c:
        rows = c.execute("""
            SELECT * FROM feature_permissions
            ORDER BY feature_id
        """).fetchall()

    body = """
    <h1>MOD MENU Permissions</h1>

    <div class="card">
      <p>
        Global status:
        <b class="{{ 'ok' if menu_enabled else 'bad' }}">
          {{ 'ENABLED' if menu_enabled else 'DISABLED' }}
        </b>
      </p>
      <p class="small">
        Disabling a feature here prevents the server from granting that
        feature permission to new/session-refresh responses.
      </p>
    </div>

    <div class="card">
      <table>
        <tr><th>ID</th><th>Feature</th><th>Global</th><th>Min Version</th><th>Save</th></tr>
        {% for r in rows %}
        <tr>
          <td>{{ r['feature_id'] }}</td>
          <td>{{ r['name'] }}</td>
          <td>
            <form method="post" action="{{ url_for('feature_update', feature_id=r['feature_id']) }}">
              <input type="hidden" name="csrf" value="{{ csrf }}">
              <select name="enabled">
                <option value="1" {{ 'selected' if r['enabled'] else '' }}>Enabled</option>
                <option value="0" {{ 'selected' if not r['enabled'] else '' }}>Disabled</option>
              </select>
          </td>
          <td><input name="min_client_version" value="{{ r['min_client_version'] }}"></td>
          <td><button class="btn primary">Save</button></form></td>
        </tr>
        {% endfor %}
      </table>
    </div>
    """
    return page("MOD MENU", render_template_string(
        body,
        rows=rows,
        menu_enabled=get_setting("menu_enabled") == "1",
        csrf=csrf_token()
    ))


@app.post("/features/<int:feature_id>/update")
@admin_required
def feature_update(feature_id):
    require_csrf()
    enabled = 1 if request.form.get("enabled") == "1" else 0
    version = request.form.get("min_client_version", "1.0.0")[:50]

    with db() as c:
        c.execute("""
            UPDATE feature_permissions
            SET enabled=?, min_client_version=?
            WHERE feature_id=?
        """, (enabled, version, feature_id))

    audit(
        "FEATURE_UPDATED",
        f"id={feature_id} enabled={enabled} min_version={version}"
    )
    return redirect(url_for("features_page"))


@app.get("/settings")
@admin_required
def settings_page():
    body = """
    <h1>Server Settings</h1>
    <div class="card">
      <form method="post">
        <input type="hidden" name="csrf" value="{{ csrf }}">
        <label>MOD MENU</label>
        <select name="menu_enabled">
          <option value="1" {{ 'selected' if menu_enabled else '' }}>Enabled</option>
          <option value="0" {{ 'selected' if not menu_enabled else '' }}>Disabled</option>
        </select>

        <label>Maintenance</label>
        <select name="maintenance">
          <option value="0" {{ 'selected' if not maintenance else '' }}>OFF</option>
          <option value="1" {{ 'selected' if maintenance else '' }}>ON</option>
        </select>

        <label>Menu Version</label>
        <input name="menu_version" value="{{ version }}">

        <label>Minimum Client Version</label>
        <input name="min_client_version" value="{{ minver }}">

        <label>Announcement</label>
        <textarea name="announcement" rows="4">{{ announcement }}</textarea>

        <button class="btn primary">Save Settings</button>
      </form>
    </div>
    """
    return page("Settings", render_template_string(
        body,
        menu_enabled=get_setting("menu_enabled") == "1",
        maintenance=get_setting("maintenance") == "1",
        version=get_setting("menu_version", "1.0.0"),
        minver=get_setting("min_client_version", "1.0.0"),
        announcement=get_setting("announcement", ""),
        csrf=csrf_token()
    ))


@app.post("/settings")
@admin_required
def settings_save():
    require_csrf()

    set_setting("menu_enabled", "1" if request.form.get("menu_enabled") == "1" else "0")
    set_setting("maintenance", "1" if request.form.get("maintenance") == "1" else "0")
    set_setting("menu_version", request.form.get("menu_version", "1.0.0")[:50])
    set_setting("min_client_version", request.form.get("min_client_version", "1.0.0")[:50])
    set_setting("announcement", request.form.get("announcement", "")[:2000])

    # Kill switch: immediately revoke all sessions.
    if get_setting("menu_enabled") != "1" or get_setting("maintenance") == "1":
        with db() as c:
            c.execute("UPDATE api_sessions SET revoked=1 WHERE revoked=0")

    audit("SETTINGS_UPDATED", "server settings changed")
    return redirect(url_for("settings_page"))


@app.get("/login-activity")
@admin_required
def login_activity_page():
    with db() as c:
        rows = c.execute("""
            SELECT created_at,event,ip,details
            FROM audit_logs
            WHERE event IN (
                'API_LOGIN_OK',
                'API_LOGIN_FAILED',
                'ADMIN_LOGIN',
                'ADMIN_LOGIN_FAILED'
            )
            ORDER BY id DESC
            LIMIT 500
        """).fetchall()

    body = """
    <h1>Login Activity</h1>
    <div class="card">
      <p class="small">
        Successful and failed login attempts with IP, masked key,
        HWID prefix and client version.
      </p>
      <div class="table-scroll">
      <table>
        <tr><th>Time</th><th>Result</th><th>IP</th><th>Details</th></tr>
        {% for r in rows %}
        <tr>
          <td>{{ r['created_at'] }}</td>
          <td>
            {% if r['event'] in ['API_LOGIN_OK','ADMIN_LOGIN'] %}
              <span class="ok">{{ r['event'] }}</span>
            {% else %}
              <span class="bad">{{ r['event'] }}</span>
            {% endif %}
          </td>
          <td><code>{{ r['ip'] or 'unknown' }}</code></td>
          <td>{{ r['details'] }}</td>
        </tr>
        {% endfor %}
      </table>
      </div>
    </div>
    """
    return page("Login Activity", render_template_string(body, rows=rows))


@app.get("/logs")
@admin_required
def logs_page():
    with db() as c:
        rows = c.execute("""
            SELECT * FROM audit_logs
            ORDER BY id DESC LIMIT 300
        """).fetchall()

    body = """
    <h1>Audit Logs</h1>
    <div class="card">
      <table>
        <tr><th>Time</th><th>Event</th><th>IP</th><th>Details</th></tr>
        {% for r in rows %}
        <tr>
          <td>{{ r['created_at'] }}</td>
          <td>{{ r['event'] }}</td>
          <td>{{ r['ip'] }}</td>
          <td>{{ r['details'] }}</td>
        </tr>
        {% endfor %}
      </table>
    </div>
    """
    return page("Logs", render_template_string(body, rows=rows, csrf=csrf_token()))


# ============================================================
# SECURITY HEADERS / ERROR RESPONSES
# ============================================================

@app.after_request
def security_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Cache-Control"] = "no-store"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "style-src 'self' 'unsafe-inline'; "
        "script-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:;"
    )
    return response


@app.errorhandler(400)
def bad_request(e):
    if request.path.startswith("/api/"):
        return jsonify({"success": False, "message": str(e)}), 400
    message = str(e)
    if "Invalid CSRF token" in message:
        return render_template_string(BASE_STYLE + """
        <div class="wrap login-shell"><div class="card login-card">
          <h1>Session expired</h1>
          <p class="small">The security token for this page is no longer valid. Reload the panel and try again.</p>
          <a class="btn primary" href="{{ url_for('keys_page') }}">Reload Keys</a>
        </div></div>
        """), 400
    return message, 400


@app.errorhandler(404)
def not_found(e):
    if request.path.startswith("/api/"):
        return jsonify({"success": False, "message": "Not found"}), 404
    return "Not found", 404


# ============================================================
# STARTUP
# ============================================================

init_db()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8080"))
    # Host must be 0.0.0.0 for Railway/Render/etc.
    app.run(host="0.0.0.0", port=port, debug=False)
