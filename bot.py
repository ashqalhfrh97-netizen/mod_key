
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

ADMIN_USER = os.environ.get("ADMIN_USER", "@X50ASD").strip()
# Set these in your hosting provider's environment variables.
# The fallback values keep older deployments bootable, but MUST be rotated
# before public use because source-code defaults are not secret.
ADMIN_PASS = os.environ.get("ADMIN_PASS", "basar2011").strip()
SECRET_KEY = os.environ.get("SECRET_KEY", "EU-HUD1-A4vDR8T9KSFTrZ1g-0-1").strip()

if not ADMIN_USER or not ADMIN_PASS or not SECRET_KEY:
    raise RuntimeError(
        "Set ADMIN_USER, ADMIN_PASS and SECRET_KEY environment variables "
        "before starting the server."
    )

app.secret_key = SECRET_KEY

# Full-page animated background GIF. You can replace this URL later
# without changing the rest of the application.
ANIME_GIF_URL = os.environ.get(
    "ANIME_GIF_URL",
    "https://giphy.com/gifs/hxh-killua-qb1eHxhUHLdsc"
).strip()
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

        CREATE TABLE IF NOT EXISTS device_ips (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            key_id INTEGER NOT NULL,
            hwid TEXT NOT NULL,
            ip TEXT NOT NULL,
            first_seen TEXT NOT NULL,
            last_seen TEXT NOT NULL,
            UNIQUE(key_id, hwid, ip),
            FOREIGN KEY(key_id) REFERENCES keys(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS bans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ban_type TEXT NOT NULL CHECK(ban_type IN ('IP','HWID')),
            value TEXT NOT NULL,
            reason TEXT DEFAULT '',
            created_at TEXT NOT NULL,
            expires_at TEXT,
            active INTEGER NOT NULL DEFAULT 1,
            created_by TEXT DEFAULT 'admin'
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

        # Safe additive migrations for databases created by older versions.
        device_columns = {
            row["name"] for row in c.execute("PRAGMA table_info(devices)").fetchall()
        }
        if "last_ip" not in device_columns:
            c.execute("ALTER TABLE devices ADD COLUMN last_ip TEXT DEFAULT ''")

        key_columns = {row["name"] for row in c.execute("PRAGMA table_info(keys)").fetchall()}
        if "is_paused" not in key_columns:
            c.execute("ALTER TABLE keys ADD COLUMN is_paused INTEGER NOT NULL DEFAULT 0")
        if "paused_remaining" not in key_columns:
            c.execute("ALTER TABLE keys ADD COLUMN paused_remaining INTEGER")

        c.execute("CREATE INDEX IF NOT EXISTS idx_device_ips_ip ON device_ips(ip)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_bans_lookup ON bans(ban_type,value,active)")

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
    # Do not trust arbitrary X-Forwarded-For headers. Enable proxy parsing
    # only when the hosting proxy is explicitly configured as trusted.
    remote = request.remote_addr or ""
    if os.environ.get("TRUST_PROXY_HEADERS", "0") == "1":
        forwarded = request.headers.get("X-Forwarded-For", "")
        candidate = forwarded.split(",")[0].strip()
        if candidate and len(candidate) <= 64:
            return candidate
    return remote


def active_ban(ban_type, value):
    if not value:
        return None
    with db() as c:
        rows = c.execute("""
            SELECT * FROM bans
            WHERE ban_type=? AND value=? AND active=1
            ORDER BY id DESC
        """, (ban_type, value)).fetchall()
        for row in rows:
            expiry = parse_iso(row["expires_at"])
            if expiry is None or expiry > now_utc():
                return row
            c.execute("UPDATE bans SET active=0 WHERE id=?", (row["id"],))
    return None


def record_device_ip(key_id, hwid, ip):
    if not ip:
        return
    stamp = iso(now_utc())
    with db() as c:
        c.execute("""
            INSERT INTO device_ips(key_id,hwid,ip,first_seen,last_seen)
            VALUES(?,?,?,?,?)
            ON CONFLICT(key_id,hwid,ip)
            DO UPDATE SET last_seen=excluded.last_seen
        """, (key_id, hwid, ip, stamp, stamp))
        c.execute("""
            UPDATE devices SET last_ip=?, last_seen=?
            WHERE key_id=? AND hwid=?
        """, (ip, stamp, key_id, hwid))


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
    if "is_paused" in row.keys() and row["is_paused"]:
        return "PAUSED"
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

    client_ip = current_client_ip()
    ip_ban = active_ban("IP", client_ip)
    hwid_ban = active_ban("HWID", hwid)
    if ip_ban or hwid_ban:
        ban = ip_ban or hwid_ban
        audit("API_LOGIN_BLOCKED", f"type={ban['ban_type']} reason={ban['reason']}", client_ip)
        return jsonify({"success": False, "message": "Access blocked by administrator"}), 403

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

    record_device_ip(key_row["id"], hwid, client_ip)
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
                k.is_paused AS key_is_paused,
                k.expires_at AS key_expires_at
            FROM api_sessions s
            JOIN keys k ON k.id=s.key_id
            WHERE s.token_hash=? AND s.revoked=0
        """, (token_hash,)).fetchone()

        if not row:
            return None, "Invalid session"

        if active_ban("IP", current_client_ip()) or active_ban("HWID", row["hwid"]):
            c.execute("UPDATE api_sessions SET revoked=1 WHERE id=?", (row["id"],))
            return None, "Access blocked by administrator"

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
html{min-height:100%;background:#050812}
body{margin:0;background:#050812;color:var(--text);font-family:Inter,system-ui,-apple-system,"Segoe UI",Arial,sans-serif;min-height:100vh;position:relative;overflow-x:hidden}
body::before{content:"";position:fixed;inset:0;z-index:0;background-image:url("{{ anime_gif_url }}");background-position:center center;background-repeat:no-repeat;background-size:cover;background-attachment:fixed;opacity:.40;filter:saturate(1.18) contrast(1.08);pointer-events:none}
body::after{content:"";position:fixed;inset:0;z-index:0;background:linear-gradient(180deg,rgba(3,6,15,.48),rgba(3,6,15,.72));pointer-events:none}

a{color:#9cc8ff;text-decoration:none}a:hover{color:#fff}
.wrap{max-width:1320px;margin:0 auto;padding:24px 16px 60px;position:relative;z-index:1}
.topbar{display:flex;align-items:center;justify-content:space-between;gap:16px;margin-bottom:18px;padding:14px 16px;background:rgba(16,25,39,.9);backdrop-filter:blur(16px);border:1px solid var(--line);border-radius:18px;position:sticky;top:12px;z-index:20;box-shadow:0 18px 55px rgba(0,0,0,.3)}
.brand{display:flex;align-items:center;gap:11px;font-weight:800}.brand-dot{width:11px;height:11px;border-radius:50%;background:var(--green);box-shadow:0 0 18px rgba(52,211,153,.75)}
.nav{display:flex;gap:7px;flex-wrap:wrap;justify-content:flex-end}.nav a,.btn{display:inline-flex;align-items:center;justify-content:center;padding:9px 12px;border-radius:10px;background:#172337;color:#eaf2ff;border:1px solid #2c3e57;cursor:pointer;font-weight:700;transition:.18s}.nav a:hover,.btn:hover{transform:translateY(-1px);border-color:#4d6b94;background:#1c2c44}
.btn.primary{background:linear-gradient(135deg,#2563eb,#3b82f6);border-color:#4c8dff}.btn.danger{background:#65152b;border-color:#a83b59}
.hero{overflow:hidden;position:relative;border:1px solid var(--line);border-radius:20px;background:#0c1421;margin-bottom:18px;box-shadow:0 18px 55px rgba(0,0,0,.28)}.hero-overlay{position:absolute;inset:0;padding:28px;display:flex;align-items:flex-end;background:linear-gradient(90deg,rgba(5,9,15,.92),rgba(5,9,15,.25) 70%,rgba(5,9,15,.65))}.hero h1{font-size:clamp(26px,4vw,44px);margin:0 0 8px}.hero p{margin:0;color:#b8c7da}
.card{background:linear-gradient(180deg,rgba(18,29,45,.97),rgba(12,20,32,.97));border:1px solid var(--line);border-radius:17px;padding:18px;margin-bottom:15px;box-shadow:0 12px 35px rgba(0,0,0,.18)}
h1,h2,h3{margin-top:0}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:13px}.stat{font-size:30px;font-weight:850;margin-top:5px}.small{color:var(--muted);font-size:13px}.row{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:10px}
input,select,textarea{width:100%;padding:11px 12px;margin:6px 0 12px;background:#09111d;color:#fff;border:1px solid #30435d;border-radius:10px;outline:none}input:focus,select:focus,textarea:focus{border-color:#5b9cff;box-shadow:0 0 0 3px rgba(59,130,246,.15)}
table{width:100%;border-collapse:separate;border-spacing:0}th,td{padding:11px;border-bottom:1px solid #243247;text-align:left;vertical-align:middle}th{color:#a9bad0;font-size:12px;text-transform:uppercase;letter-spacing:.05em}tr:hover td{background:rgba(59,130,246,.035)}
.badge{display:inline-flex;padding:4px 9px;border-radius:999px;background:#1c2a3d;border:1px solid #30435d}.ok{color:var(--green)}.bad{color:var(--red)}.inline{display:inline}code{color:#b9d7ff;background:#09111d;padding:3px 6px;border-radius:6px}.login-shell{min-height:92vh;display:grid;place-items:center}.login-card{width:min(460px,100%);padding:26px}.footer{text-align:center;color:#71839a;font-size:12px;margin-top:20px}
@media(max-width:800px){.topbar{position:static;align-items:flex-start;flex-direction:column}.nav{justify-content:flex-start}.hero-overlay{padding:20px}table{display:block;overflow-x:auto;white-space:nowrap}}
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
              <a href="{{ url_for('devices_page') }}">Devices / IPs</a>
              <a href="{{ url_for('bans_page') }}">Block System</a>
              <a href="{{ url_for('sessions_page') }}">Sessions</a>
              <a href="{{ url_for('backup_database') }}">Backup DB</a>
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
        csrf=csrf_token(),
        anime_gif_url=ANIME_GIF_URL
    )

@app.get("/login")
def login():
    if session.get("admin"):
        return redirect(url_for("dashboard"))

    return render_template_string(BASE_STYLE + """
    <div class="wrap login-shell">
      <div class="card login-card">
        <div class="hero" style="margin:-26px -26px 22px;border-radius:17px 17px 0 0;border:0">
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
    """, error=None, csrf=csrf_token(), anime_gif_url=ANIME_GIF_URL)


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
        ips = c.execute("SELECT COUNT(DISTINCT ip) n FROM device_ips").fetchone()["n"]
        blocked = c.execute("""
            SELECT COUNT(*) n FROM bans
            WHERE active=1 AND (expires_at IS NULL OR expires_at>?)
        """, (iso(now_utc()),)).fetchone()["n"]
        recent_logins = c.execute("""
            SELECT COUNT(*) n FROM audit_logs
            WHERE event='API_LOGIN_OK' AND created_at>=?
        """, (iso(now_utc() - timedelta(hours=24)),)).fetchone()["n"]

    body = """
    <div class="hero">
      <div class="hero-overlay"><div><h1>Private Control Panel</h1><p>Secure key, device and session management</p></div></div>
    </div>
    <div class="grid">
      <div class="card"><div class="small">Keys</div><div class="stat">{{ total }}</div></div>
      <div class="card"><div class="small">Active Keys</div><div class="stat">{{ active }}</div></div>
      <div class="card"><div class="small">Registered Devices</div><div class="stat">{{ devices }}</div></div>
      <div class="card"><div class="small">Live API Sessions</div><div class="stat">{{ sessions }}</div></div>
      <div class="card"><div class="small">Known IP Addresses</div><div class="stat">{{ ips }}</div></div>
      <div class="card"><div class="small">Active Bans</div><div class="stat">{{ blocked }}</div></div>
      <div class="card"><div class="small">Successful Logins (24h)</div><div class="stat">{{ recent_logins }}</div></div>
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
        ips=ips, blocked=blocked, recent_logins=recent_logins,
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
    <div class="keys-head">
      <div><h1 data-en="Keys" data-ar="المفاتيح">Keys</h1>
      <p class="small" data-en="Create and manage activation keys" data-ar="إنشاء مفاتيح التفعيل وإدارتها">Create and manage activation keys</p></div>
      <button type="button" class="btn" onclick="toggleLanguage()" id="languageBtn">العربية</button>
    </div>

    <div class="keys-banner">
      <img src="{{ anime_gif_url }}" alt="Animated banner" loading="lazy">
      <div><strong data-en="Key Control Center" data-ar="مركز إدارة المفاتيح">Key Control Center</strong>
      <p data-en="Manage devices, validity and access from one place." data-ar="تحكم بالأجهزة والصلاحية والوصول من مكان واحد.">Manage devices, validity and access from one place.</p></div>
    </div>

    {% if created_key %}
    <div class="card created-key-card">
      <div class="small" data-en="NEW ACTIVATION KEY" data-ar="مفتاح التفعيل الجديد">NEW ACTIVATION KEY</div>
      <div class="created-key-row">
        <code id="newKey">{{ created_key }}</code>
        <button type="button" class="btn primary" data-key="{{ created_key }}" onclick="copyText(this.dataset.key, this)" data-en="Copy key" data-ar="نسخ المفتاح">Copy key</button>
      </div>
      <div class="small" data-en="Save this key somewhere safe." data-ar="احفظ المفتاح في مكان آمن.">Save this key somewhere safe.</div>
    </div>
    {% endif %}

    <div class="card">
      <h2 data-en="Create a key" data-ar="إنشاء مفتاح">Create a key</h2>
      <form method="post" action="{{ url_for('create_key') }}">
        <input type="hidden" name="csrf" value="{{ csrf }}">
        <div class="row">
          <div><label data-en="Custom key (optional)" data-ar="مفتاح مخصص (اختياري)">Custom key (optional)</label><input name="key" placeholder="AK-XXXX-XXXX-XXXX"></div>
          <div><label data-en="Allowed devices" data-ar="عدد الأجهزة المسموح بها">Allowed devices</label><input type="number" name="device_limit" min="1" max="100" value="1"></div>
          <div><label data-en="Validity in days (0 = unlimited)" data-ar="الصلاحية بالأيام (0 = دائم)">Validity in days (0 = unlimited)</label><input type="number" name="days" min="0" value="30"></div>
        </div>
        <label data-en="Note" data-ar="ملاحظة">Note</label><input name="note" placeholder="Optional note">
        <button class="btn primary" data-en="Create key" data-ar="إنشاء المفتاح">Create key</button>
      </form>
    </div>

    <div class="card">
      <h2 data-en="Your keys" data-ar="المفاتيح الحالية">Your keys</h2>
      <div class="table-wrap"><table>
        <tr><th data-en="Key" data-ar="المفتاح">Key</th><th data-en="Status" data-ar="الحالة">Status</th><th data-en="Devices" data-ar="الأجهزة">Devices</th><th data-en="Expiration" data-ar="الانتهاء">Expiration</th><th data-en="Actions" data-ar="الإجراءات">Actions</th></tr>
        {% for r in rows %}
        <tr>
          <td><code>{{ r['key'] }}</code><br><span class="small">{{ r['note'] }}</span><br>
            <button type="button" class="btn tiny" data-key="{{ r['key'] }}" onclick="copyText(this.dataset.key, this)" data-en="Copy" data-ar="نسخ">Copy</button></td>
          <td><span class="badge {{ 'ok' if status(r)=='ACTIVE' else 'bad' if status(r) in ['DISABLED','EXPIRED'] else '' }}" data-status="{{ status(r) }}" data-en="{{ {'ACTIVE':'Active','PAUSED':'Paused','DISABLED':'Disabled','EXPIRED':'Expired'}.get(status(r), status(r)) }}" data-ar="{{ {'ACTIVE':'نشط','PAUSED':'متوقف مؤقتاً','DISABLED':'معطل','EXPIRED':'منتهي'}.get(status(r), status(r)) }}">{{ {'ACTIVE':'Active','PAUSED':'Paused','DISABLED':'Disabled','EXPIRED':'Expired'}.get(status(r), status(r)) }}</span></td>
          <td>{{ r['device_count'] }}/{{ r['device_limit'] }}</td>
          <td>{% if r['is_paused'] %}<span class="badge" data-en="Timer paused" data-ar="الوقت متوقف">Timer paused</span>
              {% elif r['expires_at'] %}<span class="countdown" data-exp="{{ r['expires_at'] }}">...</span><br><span class="small">{{ r['expires_at'] }}</span>
              {% else %}<span data-en="Unlimited" data-ar="دائم">Unlimited</span>{% endif %}</td>
          <td class="actions-cell">
            <form class="inline" method="post" action="{{ url_for('toggle_key', key_id=r['id']) }}"><input type="hidden" name="csrf" value="{{ csrf }}"><button class="btn tiny" data-en="{{ 'Disable' if r['active'] else 'Enable' }}" data-ar="{{ 'تعطيل' if r['active'] else 'تفعيل' }}">{{ 'Disable' if r['active'] else 'Enable' }}</button></form>
            {% if r['expires_at'] or r['is_paused'] %}
            <form class="inline" method="post" action="{{ url_for('pause_key_timer', key_id=r['id']) }}"><input type="hidden" name="csrf" value="{{ csrf }}"><button class="btn tiny" data-en="{{ 'Resume time' if r['is_paused'] else 'Pause time' }}" data-ar="{{ 'استئناف الوقت' if r['is_paused'] else 'إيقاف الوقت' }}">{{ 'Resume time' if r['is_paused'] else 'Pause time' }}</button></form>
            <form class="inline" method="post" action="{{ url_for('extend_key', key_id=r['id']) }}"><input type="hidden" name="csrf" value="{{ csrf }}"><input class="mini-input" type="number" name="days" min="1" max="3650" value="7" aria-label="Days"><button class="btn tiny" data-en="Add days" data-ar="زيادة الأيام">Add days</button></form>
            {% endif %}
            <form class="inline" method="post" action="{{ url_for('update_key_devices', key_id=r['id']) }}"><input type="hidden" name="csrf" value="{{ csrf }}"><input class="mini-input" type="number" name="device_limit" min="1" max="100" value="{{ r['device_limit'] }}" aria-label="Device limit"><button class="btn tiny" data-en="Save devices" data-ar="حفظ الأجهزة">Save devices</button></form>
            <form class="inline" method="post" action="{{ url_for('delete_key', key_id=r['id']) }}" onsubmit="return confirm('Delete this key and its devices/sessions?')"><input type="hidden" name="csrf" value="{{ csrf }}"><button class="btn danger tiny" data-en="Delete" data-ar="حذف">Delete</button></form>
          </td>
        </tr>
        {% else %}<tr><td colspan="5" class="small" data-en="No keys created yet." data-ar="لا توجد مفاتيح حالياً.">No keys created yet.</td></tr>{% endfor %}
      </table></div>
    </div>

    <style>
      .keys-head{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:14px}
      .keys-head h1{margin:0}.keys-banner{display:flex;align-items:center;gap:18px;overflow:hidden;border:1px solid var(--line);border-radius:17px;background:rgba(12,20,32,.94);margin-bottom:16px;min-height:115px}
      .keys-banner img{width:180px;height:115px;object-fit:cover}.keys-banner strong{font-size:20px}.keys-banner p{color:var(--muted);margin:6px 12px 6px 0}
      .created-key-row{display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin:10px 0}.created-key-row code{font-size:16px}
      .actions-cell{min-width:270px}.actions-cell form{margin:3px 2px}.tiny{padding:7px 9px;font-size:12px}.mini-input{width:62px!important;padding:7px!important;margin:2px!important}
      .table-wrap{overflow-x:auto}html[dir="rtl"] body{text-align:right}html[dir="rtl"] .keys-banner{flex-direction:row-reverse}html[dir="rtl"] .keys-banner p{margin:6px 0 6px 12px}
      @media(max-width:650px){.keys-banner img{width:115px;height:100px}.keys-banner{min-height:100px}.keys-banner strong{font-size:16px}}
    </style>
    <script>
    async function copyText(value, btn){
      try { await navigator.clipboard.writeText(value); }
      catch(e){ const a=document.createElement('textarea');a.value=value;a.style.position='fixed';a.style.opacity='0';document.body.appendChild(a);a.select();document.execCommand('copy');a.remove(); }
      const old=btn.textContent;btn.textContent=(document.documentElement.lang==='ar'?'تم النسخ ✓':'Copied ✓');setTimeout(()=>btn.textContent=old,1300);
    }
    function toggleLanguage(){
      const ar=document.documentElement.lang!=='ar';document.documentElement.lang=ar?'ar':'en';document.documentElement.dir=ar?'rtl':'ltr';
      document.querySelectorAll('[data-en][data-ar]').forEach(el=>{const v=ar?el.dataset.ar:el.dataset.en;if(v!==undefined)el.textContent=v;});
      document.getElementById('languageBtn').textContent=ar?'English':'العربية';
      try{localStorage.setItem('keys_panel_lang',ar?'ar':'en')}catch(e){}
    }
    (function(){let l='en';try{l=localStorage.getItem('keys_panel_lang')||'en'}catch(e){}if(l==='ar')toggleLanguage();})();
    function countdown(){document.querySelectorAll('[data-exp]').forEach(function(el){const end=new Date(el.dataset.exp).getTime();let sec=Math.max(0,Math.floor((end-Date.now())/1000));const d=Math.floor(sec/86400);sec%=86400;const h=Math.floor(sec/3600);sec%=3600;const m=Math.floor(sec/60);const s=sec%60;el.textContent=d+'d '+h+'h '+m+'m '+s+'s';});}
    countdown();setInterval(countdown,1000);
    </script>
    """
    return page("Keys", render_template_string(
        body, rows=rows, status=key_status, csrf=csrf_token(), anime_gif_url=ANIME_GIF_URL
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


@app.post("/keys/<int:key_id>/devices")
@admin_required
def update_key_devices(key_id):
    require_csrf()
    try:
        limit = max(1, min(100, int(request.form.get("device_limit", "1"))))
    except (TypeError, ValueError):
        abort(400)
    with db() as c:
        row = c.execute("SELECT id FROM keys WHERE id=?", (key_id,)).fetchone()
        if not row:
            abort(404)
        c.execute("UPDATE keys SET device_limit=? WHERE id=?", (limit, key_id))
    audit("KEY_DEVICE_LIMIT_UPDATED", f"key_id={key_id} limit={limit}")
    return redirect(url_for("keys_page"))


@app.post("/keys/<int:key_id>/extend")
@admin_required
def extend_key(key_id):
    require_csrf()
    try:
        days = max(1, min(3650, int(request.form.get("days", "7"))))
    except (TypeError, ValueError):
        abort(400)
    with db() as c:
        row = c.execute("SELECT * FROM keys WHERE id=?", (key_id,)).fetchone()
        if not row:
            abort(404)
        if row["is_paused"]:
            remaining = int(row["paused_remaining"] or 0) + days * 86400
            c.execute("UPDATE keys SET paused_remaining=? WHERE id=?", (remaining, key_id))
        elif row["expires_at"]:
            old_exp = parse_iso(row["expires_at"])
            base = old_exp if old_exp and old_exp > now_utc() else now_utc()
            c.execute("UPDATE keys SET expires_at=? WHERE id=?", (iso(base + timedelta(days=days)), key_id))
        else:
            # A permanent key remains permanent; no artificial expiry is added.
            pass
    audit("KEY_EXTENDED", f"key_id={key_id} days={days}")
    return redirect(url_for("keys_page"))


@app.post("/keys/<int:key_id>/pause")
@admin_required
def pause_key_timer(key_id):
    require_csrf()
    with db() as c:
        row = c.execute("SELECT * FROM keys WHERE id=?", (key_id,)).fetchone()
        if not row:
            abort(404)
        if row["is_paused"]:
            remaining = max(1, int(row["paused_remaining"] or 0))
            c.execute("UPDATE keys SET is_paused=0, paused_remaining=NULL, expires_at=? WHERE id=?",
                      (iso(now_utc() + timedelta(seconds=remaining)), key_id))
            action = "resumed"
        elif row["expires_at"]:
            exp = parse_iso(row["expires_at"])
            remaining = max(0, int((exp - now_utc()).total_seconds())) if exp else 0
            c.execute("UPDATE keys SET is_paused=1, paused_remaining=?, expires_at=NULL WHERE id=?",
                      (remaining, key_id))
            action = "paused"
        else:
            return redirect(url_for("keys_page"))
    audit("KEY_TIMER_" + action.upper(), f"key_id={key_id}")
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
    term = request.args.get("q", "").strip()[:200]
    with db() as c:
        if term:
            like = f"%{term}%"
            rows = c.execute("""
                SELECT d.*, k.key,
                       COALESCE((SELECT GROUP_CONCAT(ip, ', ')
                         FROM device_ips x
                         WHERE x.key_id=d.key_id AND x.hwid=d.hwid), '') AS ips
                FROM devices d JOIN keys k ON k.id=d.key_id
                WHERE d.hwid LIKE ? OR k.key LIKE ? OR d.last_ip LIKE ?
                   OR EXISTS (SELECT 1 FROM device_ips x
                              WHERE x.key_id=d.key_id AND x.hwid=d.hwid AND x.ip LIKE ?)
                ORDER BY d.last_seen DESC
            """, (like, like, like, like)).fetchall()
        else:
            rows = c.execute("""
                SELECT d.*, k.key,
                       COALESCE((SELECT GROUP_CONCAT(ip, ', ')
                         FROM device_ips x
                         WHERE x.key_id=d.key_id AND x.hwid=d.hwid), '') AS ips
                FROM devices d JOIN keys k ON k.id=d.key_id
                ORDER BY d.last_seen DESC
            """).fetchall()

    body = """
    <h1>Devices & IP Addresses</h1>
    <div class="card">
      <form method="get">
        <input name="q" value="{{ term }}" placeholder="Search IP, HWID or key">
        <button class="btn primary">Search</button>
        <a class="btn" href="{{ url_for('devices_page') }}">Clear</a>
        <a class="btn" href="{{ url_for('export_devices') }}">Export CSV</a>
      </form>
      <div class="table-scroll"><table>
        <tr><th>Key</th><th>HWID</th><th>IP addresses</th><th>First seen</th><th>Last seen</th><th>Actions</th></tr>
        {% for r in rows %}
        <tr>
          <td><code>{{ r['key'] }}</code></td>
          <td><code>{{ r['hwid'] }}</code> <button type="button" class="btn" onclick="copyText({{ r['hwid']|tojson }})">Copy</button></td>
          <td>
            {% if r['ips'] %}
              {% for ip in r['ips'].split(', ') %}
                <div><code>{{ ip }}</code> <button type="button" class="btn" onclick="copyText({{ ip|tojson }})">Copy</button>
                <form class="inline" method="post" action="{{ url_for('create_ban') }}">
                  <input type="hidden" name="csrf" value="{{ csrf }}">
                  <input type="hidden" name="ban_type" value="IP">
                  <input type="hidden" name="value" value="{{ ip }}">
                  <input type="hidden" name="duration" value="permanent">
                  <input type="hidden" name="reason" value="Blocked from device list">
                  <button class="btn danger">Block IP</button>
                </form></div>
              {% endfor %}
            {% else %}<span class="small">No IP recorded yet</span>{% endif %}
          </td>
          <td>{{ r['first_seen'] }}</td><td>{{ r['last_seen'] }}</td>
          <td>
            <form class="inline" method="post" action="{{ url_for('create_ban') }}">
              <input type="hidden" name="csrf" value="{{ csrf }}">
              <input type="hidden" name="ban_type" value="HWID">
              <input type="hidden" name="value" value="{{ r['hwid'] }}">
              <input type="hidden" name="duration" value="permanent">
              <input type="hidden" name="reason" value="Blocked from device list">
              <button class="btn danger">Block HWID</button>
            </form>
            <form class="inline" method="post" action="{{ url_for('delete_device', device_id=r['id']) }}">
              <input type="hidden" name="csrf" value="{{ csrf }}">
              <button class="btn">Unregister</button>
            </form>
          </td>
        </tr>
        {% endfor %}
      </table></div>
    </div>
    <script>
    async function copyText(value){
      try { await navigator.clipboard.writeText(value); }
      catch(e){
        const t=document.createElement('textarea'); t.value=value;
        document.body.appendChild(t); t.select(); document.execCommand('copy'); t.remove();
      }
    }
    </script>
    """
    return page("Devices & IPs", render_template_string(
        body, rows=rows, term=term, csrf=csrf_token()
    ))


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
# BLOCK SYSTEM / SESSIONS / EXPORTS / BACKUP
# ============================================================

@app.get("/bans")
@admin_required
def bans_page():
    with db() as c:
        rows = c.execute("""
            SELECT * FROM bans ORDER BY id DESC LIMIT 500
        """).fetchall()
    body = """
    <h1>IP / HWID Block System</h1>
    <div class="card">
      <h2>Create a ban</h2>
      <form method="post" action="{{ url_for('create_ban') }}">
        <input type="hidden" name="csrf" value="{{ csrf }}">
        <div class="row">
          <div><label>Type</label><select name="ban_type"><option>IP</option><option>HWID</option></select></div>
          <div><label>Address / HWID</label><input name="value" required maxlength="256"></div>
          <div><label>Duration</label><select name="duration">
            <option value="1h">1 hour</option><option value="24h">24 hours</option>
            <option value="7d">7 days</option><option value="permanent">Permanent</option>
          </select></div>
        </div>
        <label>Reason</label><input name="reason" maxlength="500" placeholder="Reason for blocking">
        <button class="btn danger">Apply ban</button>
      </form>
    </div>
    <div class="card"><h2>Ban records</h2><div class="table-scroll"><table>
      <tr><th>Type</th><th>Value</th><th>Reason</th><th>Created</th><th>Expires</th><th>Status</th><th>Action</th></tr>
      {% for r in rows %}
      <tr><td>{{ r['ban_type'] }}</td><td><code>{{ r['value'] }}</code></td>
      <td>{{ r['reason'] }}</td><td>{{ r['created_at'] }}</td>
      <td>{{ r['expires_at'] or 'Permanent' }}</td>
      <td>{{ 'Active' if r['active'] and (not r['expires_at'] or r['expires_at'] > now) else 'Inactive / Expired' }}</td>
      <td>{% if r['active'] %}<form method="post" action="{{ url_for('remove_ban', ban_id=r['id']) }}">
        <input type="hidden" name="csrf" value="{{ csrf }}"><button class="btn primary">Unblock</button>
      </form>{% endif %}</td></tr>
      {% endfor %}
    </table></div></div>
    """
    return page("Block System", render_template_string(
        body, rows=rows, csrf=csrf_token(), now=iso(now_utc())
    ))


@app.post("/bans/create")
@admin_required
def create_ban():
    require_csrf()
    ban_type = request.form.get("ban_type", "").upper()
    value = request.form.get("value", "").strip()
    reason = request.form.get("reason", "").strip()[:500]
    duration = request.form.get("duration", "permanent")
    if ban_type not in ("IP", "HWID") or not value or len(value) > 256:
        abort(400, "Invalid ban type or value")
    expiry = None
    durations = {"1h": timedelta(hours=1), "24h": timedelta(hours=24), "7d": timedelta(days=7)}
    if duration in durations:
        expiry = iso(now_utc() + durations[duration])
    elif duration != "permanent":
        abort(400, "Invalid ban duration")
    with db() as c:
        c.execute("""
            INSERT INTO bans(ban_type,value,reason,created_at,expires_at,active,created_by)
            VALUES(?,?,?,?,?,1,?)
        """, (ban_type, value, reason, iso(now_utc()), expiry, ADMIN_USER))
        if ban_type == "HWID":
            c.execute("UPDATE api_sessions SET revoked=1 WHERE hwid=?", (value,))
        elif ban_type == "IP":
            c.execute("""
                UPDATE api_sessions SET revoked=1
                WHERE hwid IN (SELECT hwid FROM device_ips WHERE ip=?)
            """, (value,))
    audit("BAN_CREATED", f"type={ban_type} value={value[:80]} duration={duration} reason={reason}")
    return redirect(url_for("bans_page"))


@app.post("/bans/<int:ban_id>/remove")
@admin_required
def remove_ban(ban_id):
    require_csrf()
    with db() as c:
        c.execute("UPDATE bans SET active=0 WHERE id=?", (ban_id,))
    audit("BAN_REMOVED", f"ban_id={ban_id}")
    return redirect(url_for("bans_page"))


@app.get("/sessions")
@admin_required
def sessions_page():
    with db() as c:
        rows = c.execute("""
            SELECT s.id,s.hwid,s.created_at,s.expires_at,s.last_seen,s.revoked,k.key
            FROM api_sessions s JOIN keys k ON k.id=s.key_id
            ORDER BY s.id DESC LIMIT 500
        """).fetchall()
    body = """
    <h1>API Sessions</h1><div class="card"><div class="table-scroll"><table>
      <tr><th>Key</th><th>HWID</th><th>Created</th><th>Last seen</th><th>Expires</th><th>Status</th><th>Action</th></tr>
      {% for r in rows %}<tr><td><code>{{ r['key'] }}</code></td><td><code>{{ r['hwid'] }}</code></td>
      <td>{{ r['created_at'] }}</td><td>{{ r['last_seen'] }}</td><td>{{ r['expires_at'] }}</td>
      <td>{{ 'Revoked' if r['revoked'] else 'Active / may expire' }}</td><td>
      {% if not r['revoked'] %}<form method="post" action="{{ url_for('revoke_session', session_id=r['id']) }}">
      <input type="hidden" name="csrf" value="{{ csrf }}"><button class="btn danger">Revoke</button></form>{% endif %}
      </td></tr>{% endfor %}
    </table></div></div>
    """
    return page("Sessions", render_template_string(body, rows=rows, csrf=csrf_token()))


@app.post("/sessions/<int:session_id>/revoke")
@admin_required
def revoke_session(session_id):
    require_csrf()
    with db() as c:
        c.execute("UPDATE api_sessions SET revoked=1 WHERE id=?", (session_id,))
    audit("SESSION_REVOKED", f"session_id={session_id}")
    return redirect(url_for("sessions_page"))


@app.get("/export/devices.csv")
@admin_required
def export_devices():
    import csv
    import io
    with db() as c:
        rows = c.execute("""
            SELECT k.key,d.hwid,d.first_seen,d.last_seen,d.last_ip,
                   COALESCE((SELECT GROUP_CONCAT(ip, ';') FROM device_ips x
                     WHERE x.key_id=d.key_id AND x.hwid=d.hwid),'') AS all_ips
            FROM devices d JOIN keys k ON k.id=d.key_id
            ORDER BY d.last_seen DESC
        """).fetchall()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["key", "hwid", "first_seen", "last_seen", "last_ip", "all_ips"])
    for row in rows:
        writer.writerow([row["key"], row["hwid"], row["first_seen"],
                         row["last_seen"], row["last_ip"], row["all_ips"]])
    response = make_response("\\ufeff" + output.getvalue())
    response.headers["Content-Type"] = "text/csv; charset=utf-8"
    response.headers["Content-Disposition"] = "attachment; filename=devices.csv"
    audit("DEVICES_EXPORTED", f"rows={len(rows)}")
    return response


@app.get("/backup")
@admin_required
def backup_database():
    import tempfile
    from flask import send_file
    fd, path = tempfile.mkstemp(prefix="server-backup-", suffix=".db")
    os.close(fd)
    try:
        with sqlite3.connect(path) as target:
            with db() as origin:
                origin.backup(target)
        audit("DATABASE_BACKUP", "database backup downloaded")
        response = send_file(path, as_attachment=True, download_name="server-backup.db",
                             mimetype="application/octet-stream")
        response.call_on_close(lambda: os.path.exists(path) and os.remove(path))
        return response
    except Exception:
        if os.path.exists(path):
            os.remove(path)
        raise


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
        "img-src 'self' data: https://media.tenor.com;"
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
