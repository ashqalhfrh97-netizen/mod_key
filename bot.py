
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
    "https://media.tenor.com/images/7f3d3c7c7e5f4f7d8e7f7c6c4a0b0d4b/tenor.gif"
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
        audit("API_LOGIN_RATE_LIMIT", "login rate limit exceeded", current_client_ip())
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
    client_ip = current_client_ip()
    if active_ban("IP", client_ip):
        return None, "Access temporarily blocked"

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
            stamp = iso(now_utc())
            c.execute(
                "INSERT INTO audit_logs(created_at,event,ip,details) VALUES(?,?,?,?)",
                (stamp, "API_INVALID_SESSION", client_ip, "Unknown or revoked bearer token")
            )
            since = iso(now_utc() - timedelta(minutes=10))
            attempts = c.execute("""
                SELECT COUNT(*) AS n FROM audit_logs
                WHERE event='API_INVALID_SESSION' AND ip=? AND created_at>=?
            """, (client_ip, since)).fetchone()["n"]
            if attempts == 10:
                expiry = iso(now_utc() + timedelta(minutes=15))
                c.execute("""
                    INSERT INTO bans(ban_type,value,reason,created_at,expires_at,active,created_by)
                    VALUES('IP',?,?,?, ?,1,'security-monitor')
                """, (client_ip, "10 invalid session tokens within 10 minutes", stamp, expiry))
                c.execute("""
                    INSERT INTO audit_logs(created_at,event,ip,details) VALUES(?,?,?,?)
                """, (stamp, "AUTO_TEMP_IP_BLOCK", client_ip, "15-minute block after repeated invalid session tokens"))
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
:root{--bg:#100f16;--panel:#191722;--line:#393342;--text:#f5f0f7;--muted:#b0a6b8;--blue:#a78bfa;--green:#70d6ad;--red:#ff8295;--amber:#e7ad69}
*{box-sizing:border-box}html{scroll-behavior:smooth}
html{min-height:100%;background:#100f16}
body{margin:0;background:#100f16;color:var(--text);font-family:Inter,system-ui,-apple-system,"Segoe UI",Arial,sans-serif;min-height:100vh;position:relative;overflow-x:hidden}
body::before{content:"";position:fixed;inset:0;z-index:0;background-image:url("{{ anime_gif_url }}");background-position:center center;background-repeat:no-repeat;background-size:cover;background-attachment:fixed;opacity:.52;filter:saturate(.88) contrast(1.06);pointer-events:none}
body::after{content:"";position:fixed;inset:0;z-index:0;background:linear-gradient(135deg,rgba(14,11,20,.58),rgba(18,14,25,.76));pointer-events:none}

a{color:#d2b6ff;text-decoration:none}a:hover{color:#fff}
.wrap{max-width:1320px;margin:0 auto;padding:24px 16px 60px;position:relative;z-index:1}
.topbar{display:flex;align-items:center;justify-content:space-between;gap:16px;margin-bottom:18px;padding:14px 16px;background:rgba(25,22,34,.88);backdrop-filter:blur(16px);border:1px solid var(--line);border-radius:18px;position:sticky;top:12px;z-index:20;box-shadow:0 18px 55px rgba(0,0,0,.3)}
.brand{display:flex;align-items:center;gap:11px;font-weight:800}.brand-dot{width:11px;height:11px;border-radius:50%;background:var(--green);box-shadow:0 0 18px rgba(52,211,153,.75)}
.nav{display:flex;gap:7px;flex-wrap:wrap;justify-content:flex-end}.nav a,.btn{display:inline-flex;align-items:center;justify-content:center;padding:9px 12px;border-radius:10px;background:#282332;color:#f5eff8;border:1px solid #494052;cursor:pointer;font-weight:700;transition:.18s}.nav a:hover,.btn:hover{transform:translateY(-1px);border-color:#8f76ad;background:#342b3d}
.btn.primary{background:linear-gradient(135deg,#76569b,#a17bb8);border-color:#b99bd0}.btn.danger{background:#642b3c;border-color:#a65b70}
.hero{overflow:hidden;position:relative;border:1px solid var(--line);border-radius:20px;background:rgba(24,20,31,.84);margin-bottom:18px;box-shadow:0 18px 55px rgba(0,0,0,.28)}.hero-overlay{position:absolute;inset:0;padding:28px;display:flex;align-items:flex-end;background:linear-gradient(90deg,rgba(5,9,15,.92),rgba(5,9,15,.25) 70%,rgba(5,9,15,.65))}.hero h1{font-size:clamp(26px,4vw,44px);margin:0 0 8px}.hero p{margin:0;color:#b8c7da}
.card{background:linear-gradient(180deg,rgba(31,27,40,.94),rgba(21,19,29,.96));border:1px solid var(--line);border-radius:17px;padding:18px;margin-bottom:15px;box-shadow:0 12px 35px rgba(0,0,0,.18)}
h1,h2,h3{margin-top:0}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:13px}.stat{font-size:30px;font-weight:850;margin-top:5px}.small{color:var(--muted);font-size:13px}.row{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:10px}
input,select,textarea{width:100%;padding:11px 12px;margin:6px 0 12px;background:#17141f;color:#f8f2fa;border:1px solid #494052;border-radius:10px;outline:none}input:focus,select:focus,textarea:focus{border-color:#b99bd0;box-shadow:0 0 0 3px rgba(167,139,250,.15)}
table{width:100%;border-collapse:separate;border-spacing:0}th,td{padding:11px;border-bottom:1px solid #393342;text-align:left;vertical-align:middle}th{color:#c0b5c8;font-size:12px;text-transform:uppercase;letter-spacing:.05em}tr:hover td{background:rgba(167,139,250,.045)}
.badge{display:inline-flex;padding:4px 9px;border-radius:999px;background:#302a38;border:1px solid #494052}.ok{color:var(--green)}.bad{color:var(--red)}.inline{display:inline}code{color:#e0c8f5;background:#17141f;padding:3px 6px;border-radius:6px}.login-shell{min-height:92vh;display:grid;place-items:center}.login-card{width:min(460px,100%);padding:26px}.footer{text-align:center;color:#93899c;font-size:12px;margin-top:20px}
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
              <a href="{{ url_for('security_monitor_page') }}">Security Monitor</a>
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
        audit("ADMIN_LOGIN_RATE_LIMIT", "admin login rate limit exceeded", current_client_ip())
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
    <div class="card"><h2>Security Monitor</h2><p class="small">Review failed logins, invalid sessions and temporary automatic blocks.</p><a class="btn primary" href="{{ url_for('security_monitor_page') }}">Open Security Monitor</a></div>

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
    # Permanent keys have no expiry, so extending them leaves t
