
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
from urllib.request import Request as URLRequest, urlopen
from urllib.error import URLError, HTTPError
import threading
from io import BytesIO

from flask import (
    Flask, request, jsonify, session, redirect,
    url_for, render_template_string, abort, make_response, send_file
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
#   TELEGRAM_BOT_TOKEN=...
#   TELEGRAM_ADMIN_ID=123456789
#   TELEGRAM_POLLING=1  (single server instance)
#
# IMPORTANT:
# - The server is the source of truth for keys/expiry/permissions.
# - Never put ADMIN_PASS or SECRET_KEY inside the APK.
# - HTTPS should be provided by your host (Railway, Render, etc.).
# ============================================================

app = Flask(__name__)

ADMIN_USER = os.environ.get("ADMIN_USER", "@X50ASD").strip()
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
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "8959881524:AAHJKmUz59xbPicuodo-W6prLRg-lJDnbyc").strip()
TELEGRAM_ADMIN_ID = os.environ.get("TELEGRAM_ADMIN_ID", "8299101176").strip()
TELEGRAM_BOT_USERNAME = os.environ.get("TELEGRAM_BOT_USERNAME", "MoldesKeysBot").strip().lstrip("@")
TELEGRAM_BOT_NAME = os.environ.get("TELEGRAM_BOT_NAME", "Key EX25").strip()
TELEGRAM_POLLING = os.environ.get("TELEGRAM_POLLING", "1") == "1"
BOT_POLL_OFFSET = 0
BOT_THREAD_STARTED = False
BOT_THREAD_LOCK = threading.Lock()

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

        c.executescript("""
        CREATE TABLE IF NOT EXISTS telegram_users (
            telegram_id INTEGER PRIMARY KEY,
            username TEXT DEFAULT '',
            first_name TEXT DEFAULT '',
            last_name TEXT DEFAULT '',
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS subscription_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id INTEGER NOT NULL,
            plan TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            payment_proof TEXT DEFAULT '',
            created_at TEXT NOT NULL,
            reviewed_at TEXT DEFAULT '',
            admin_note TEXT DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS subscriptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            request_id INTEGER NOT NULL UNIQUE,
            telegram_id INTEGER NOT NULL,
            key_id INTEGER NOT NULL,
            plan TEXT NOT NULL,
            starts_at TEXT NOT NULL,
            expires_at TEXT,
            device_limit INTEGER NOT NULL DEFAULT 1,
            active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            FOREIGN KEY(request_id) REFERENCES subscription_requests(id),
            FOREIGN KEY(key_id) REFERENCES keys(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS telegram_link_codes (
            code_hash TEXT PRIMARY KEY,
            hwid TEXT NOT NULL,
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            telegram_id INTEGER,
            linked_at TEXT DEFAULT '',
            used_at TEXT DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS idx_subscriptions_telegram
            ON subscriptions(telegram_id, active);
        CREATE INDEX IF NOT EXISTS idx_requests_status
            ON subscription_requests(status, created_at);
        """)

        # Safe additive migration for databases created by earlier versions.
        user_columns = {row["name"] for row in c.execute("PRAGMA table_info(telegram_users)").fetchall()}
        if "photo_file_id" not in user_columns:
            c.execute("ALTER TABLE telegram_users ADD COLUMN photo_file_id TEXT DEFAULT ''")

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
# TELEGRAM SUBSCRIPTIONS
# ============================================================

PLAN_DAYS = {"week": 7, "month": 30, "year": 365, "lifetime": 0}
PLAN_LABELS = {"week": "Week", "month": "Month", "year": "Year", "lifetime": "Lifetime"}


def telegram_api(method, payload=None):
    if not TELEGRAM_BOT_TOKEN:
        raise RuntimeError("Telegram bot is not configured")
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/{method}"
    data = json.dumps(payload or {}).encode("utf-8")
    req = URLRequest(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
    with urlopen(req, timeout=40) as response:
        result = json.loads(response.read().decode("utf-8"))
    if not result.get("ok"):
        raise RuntimeError(result.get("description", "Telegram API error"))
    return result.get("result")


def bot_send(chat_id, text, reply_markup=None):
    payload = {"chat_id": int(chat_id), "text": text}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    return telegram_api("sendMessage", payload)


def save_telegram_user(user):
    tid = int(user.get("id"))
    with db() as c:
        c.execute("""
            INSERT INTO telegram_users(telegram_id,username,first_name,last_name,updated_at)
            VALUES(?,?,?,?,?)
            ON CONFLICT(telegram_id) DO UPDATE SET
              username=excluded.username, first_name=excluded.first_name,
              last_name=excluded.last_name, updated_at=excluded.updated_at
        """, (tid, user.get("username", ""), user.get("first_name", ""),
              user.get("last_name", ""), iso(now_utc())))


def refresh_telegram_photo(telegram_id):
    """Store the largest available profile-photo file_id; the bot token stays server-side."""
    if not TELEGRAM_BOT_TOKEN:
        return
    try:
        result = telegram_api("getUserProfilePhotos", {"user_id": int(telegram_id), "offset": 0, "limit": 1})
        photos = result.get("photos") or []
        file_id = photos[0][-1].get("file_id", "") if photos and photos[0] else ""
        with db() as c:
            c.execute("UPDATE telegram_users SET photo_file_id=? WHERE telegram_id=?", (file_id, int(telegram_id)))
    except Exception as exc:
        audit("TELEGRAM_PHOTO_ERROR", str(exc)[:300])


def active_subscription(telegram_id):
    with db() as c:
        rows = c.execute("""
            SELECT s.*, k.active AS key_active, k.expires_at AS key_expires_at
            FROM subscriptions s JOIN keys k ON k.id=s.key_id
            WHERE s.telegram_id=? AND s.active=1
            ORDER BY s.id DESC
        """, (int(telegram_id),)).fetchall()
    for row in rows:
        exp = parse_iso(row["expires_at"])
        if row["key_active"] and (not exp or exp > now_utc()):
            return row
    return None


def approve_subscription_request(request_id, reviewer="admin"):
    with db() as c:
        req = c.execute("SELECT * FROM subscription_requests WHERE id=?", (request_id,)).fetchone()
        if not req:
            return False, "Request not found"
        if req["status"] != "pending":
            return False, "Request is already reviewed"
        days = PLAN_DAYS.get(req["plan"])
        if days is None:
            return False, "Invalid plan"
        try:
            device_limit = max(1, min(100, int(get_setting("telegram_device_limit", "1"))))
        except ValueError:
            device_limit = 1
        key_value = generate_key()
        created = now_utc()
        expires = None if days == 0 else iso(created + timedelta(days=days))
        note = f"Telegram:{req['telegram_id']} | Request:{request_id}"
        cur = c.execute("""
            INSERT INTO keys(key,active,device_limit,expires_at,created_at,note)
            VALUES(?,1,?,?,?,?)
        """, (key_value, device_limit, expires, iso(created), note))
        key_id = cur.lastrowid
        c.execute("""
            INSERT INTO subscriptions(request_id,telegram_id,key_id,plan,starts_at,expires_at,device_limit,active,created_at)
            VALUES(?,?,?,?,?,?,?,1,?)
        """, (request_id, req["telegram_id"], key_id, req["plan"], iso(created), expires, device_limit, iso(created)))
        c.execute("UPDATE subscription_requests SET status='approved', reviewed_at=? WHERE id=?",
                  (iso(created), request_id))
    audit("TELEGRAM_SUBSCRIPTION_APPROVED", f"request={request_id} telegram_id={req['telegram_id']} by={reviewer}")
    return True, {"telegram_id": req["telegram_id"], "expires_at": expires, "device_limit": device_limit}


def reject_subscription_request(request_id, reviewer="admin"):
    with db() as c:
        cur = c.execute("""
            UPDATE subscription_requests SET status='rejected', reviewed_at=?
            WHERE id=? AND status='pending'
        """, (iso(now_utc()), request_id))
        changed = cur.rowcount
        row = c.execute("SELECT telegram_id FROM subscription_requests WHERE id=?", (request_id,)).fetchone()
    if changed:
        audit("TELEGRAM_SUBSCRIPTION_REJECTED", f"request={request_id} by={reviewer}")
        return True, row["telegram_id"] if row else None
    return False, None


def bot_handle_update(update):
    message = update.get("message") or update.get("edited_message")
    callback = update.get("callback_query")
    if callback:
        user = callback.get("from", {})
        save_telegram_user(user)
        refresh_telegram_photo(user.get("id"))
        data = callback.get("data", "")
        callback_id = callback.get("id")
        if data.startswith("plan:"):
            plan = data.split(":", 1)[1]
            if plan not in PLAN_DAYS:
                telegram_api("answerCallbackQuery", {"callback_query_id": callback_id, "text": "Invalid plan"})
                return
            with db() as c:
                cur = c.execute("""
                    INSERT INTO subscription_requests(telegram_id,plan,status,created_at)
                    VALUES(?,?,'pending',?)
                """, (int(user["id"]), plan, iso(now_utc())))
                request_id = cur.lastrowid
            telegram_api("answerCallbackQuery", {"callback_query_id": callback_id, "text": "Request created"})
            instructions = get_setting("payment_instructions", "Please send your payment proof or transaction reference in this chat.")
            bot_send(user["id"], f"Your {PLAN_LABELS[plan]} subscription request #{request_id} has been created.\n\n{instructions}\n\nAfter sending proof, wait for admin approval.")
            if TELEGRAM_ADMIN_ID:
                proof_text = f"New subscription request #{request_id}\nUser: {user.get('first_name','')} (@{user.get('username','')})\nTelegram ID: {user['id']}\nPlan: {PLAN_LABELS[plan]}\nWaiting for payment proof; approval buttons will be sent after proof arrives."
                bot_send(TELEGRAM_ADMIN_ID, proof_text)
            return
        if data.startswith("req:"):
            if not TELEGRAM_ADMIN_ID or str(user.get("id")) != TELEGRAM_ADMIN_ID:
                telegram_api("answerCallbackQuery", {"callback_query_id": callback_id, "text": "Not authorized", "show_alert": True})
                return
            _, action, raw_id = data.split(":", 2)
            try:
                request_id = int(raw_id)
            except ValueError:
                return
            if action == "approve":
                ok, result = approve_subscription_request(request_id, str(user.get("id")))
                if ok:
                    expiry = result["expires_at"] or "Never"
                    bot_send(result["telegram_id"], f"Your subscription is approved!\nPlan activated. Expiration: {expiry}\n\nOpen the app, generate a temporary link code, then send that code here to connect your account.")
                    telegram_api("answerCallbackQuery", {"callback_query_id": callback_id, "text": "Approved"})
                    bot_send(user["id"], f"Request #{request_id} approved. Device limit: {result['device_limit']}.")
                else:
                    telegram_api("answerCallbackQuery", {"callback_query_id": callback_id, "text": str(result), "show_alert": True})
            elif action == "reject":
                ok, target = reject_subscription_request(request_id, str(user.get("id")))
                if ok and target:
                    bot_send(target, f"Your subscription request #{request_id} was not approved. Contact the administrator if you think this is a mistake.")
                    telegram_api("answerCallbackQuery", {"callback_query_id": callback_id, "text": "Rejected"})
                else:
                    telegram_api("answerCallbackQuery", {"callback_query_id": callback_id, "text": "Request already reviewed", "show_alert": True})
            return
    if not message:
        return
    user = message.get("from", {})
    chat_id = message.get("chat", {}).get("id")
    if not user or not chat_id:
        return
    save_telegram_user(user)
    refresh_telegram_photo(user.get("id"))
    text_value = (message.get("text") or "").strip()
    if text_value.startswith("/start"):
        markup = {"inline_keyboard": [
            [{"text": "1 Week", "callback_data": "plan:week"}, {"text": "1 Month", "callback_data": "plan:month"}],
            [{"text": "1 Year", "callback_data": "plan:year"}, {"text": "Lifetime", "callback_data": "plan:lifetime"}]
        ]}
        bot_send(chat_id, "Welcome! Choose your subscription plan:", markup)
        return
    # A payment proof can be text, a transaction reference, or a Telegram photo.
    proof = text_value
    if message.get("photo"):
        proof = "photo_file_id:" + message["photo"][-1].get("file_id", "")
    if proof:
        with db() as c:
            req = c.execute("""
                SELECT * FROM subscription_requests
                WHERE telegram_id=? AND status='pending'
                ORDER BY id DESC LIMIT 1
            """, (int(user["id"]),)).fetchone()
            if req:
                c.execute("UPDATE subscription_requests SET payment_proof=? WHERE id=?", (proof[:1500], req["id"]))
        if req:
            bot_send(chat_id, "Payment proof received. Please wait for manual admin review.")
            if TELEGRAM_ADMIN_ID:
                preview = proof[:500] if not proof.startswith("photo_file_id:") else "Photo proof attached in the user's chat."
                markup = {"inline_keyboard": [[
                    {"text": "Approve", "callback_data": f"req:approve:{req['id']}"},
                    {"text": "Reject", "callback_data": f"req:reject:{req['id']}"}
                ]]}
                bot_send(TELEGRAM_ADMIN_ID, f"Payment proof for request #{req['id']}\nTelegram ID: {user['id']}\nPlan: {PLAN_LABELS.get(req['plan'], req['plan'])}\nProof: {preview}", markup)
            return
    # Link codes are short, one-time codes generated by the app.
    if text_value and len(text_value) <= 32 and text_value.replace("-", "").isalnum():
        digest = hash_token(text_value.upper())
        with db() as c:
            link = c.execute("SELECT * FROM telegram_link_codes WHERE code_hash=?", (digest,)).fetchone()
            sub = active_subscription(user["id"])
            if link and not link["used_at"] and parse_iso(link["expires_at"]) and parse_iso(link["expires_at"]) > now_utc() and sub:
                c.execute("UPDATE telegram_link_codes SET telegram_id=?,linked_at=? WHERE code_hash=? AND telegram_id IS NULL",
                          (int(user["id"]), iso(now_utc()), digest))
                linked = c.execute("SELECT changes() n").fetchone()["n"] == 1
            else:
                linked = False
        if linked:
            bot_send(chat_id, "Account linked successfully! Return to the app; it will finish signing you in.")
        else:
            bot_send(chat_id, "Code not accepted. Check that your subscription is approved and the code is still valid.")


def telegram_poll_loop():
    global BOT_POLL_OFFSET
    while True:
        try:
            updates = telegram_api("getUpdates", {"offset": BOT_POLL_OFFSET, "timeout": 25, "allowed_updates": ["message", "callback_query"]})
            for item in updates or []:
                BOT_POLL_OFFSET = max(BOT_POLL_OFFSET, int(item.get("update_id", 0)) + 1)
                try:
                    bot_handle_update(item)
                except Exception as exc:
                    audit("TELEGRAM_UPDATE_ERROR", str(exc)[:500])
        except Exception as exc:
            audit("TELEGRAM_POLL_ERROR", str(exc)[:500])
            time.sleep(5)


def start_telegram_polling():
    global BOT_THREAD_STARTED
    if not TELEGRAM_POLLING or not TELEGRAM_BOT_TOKEN or not TELEGRAM_ADMIN_ID:
        return
    with BOT_THREAD_LOCK:
        if BOT_THREAD_STARTED:
            return
        thread = threading.Thread(target=telegram_poll_loop, daemon=True, name="telegram-polling")
        thread.start()
        BOT_THREAD_STARTED = True


@app.post("/api/v1/telegram/link-code")
def api_create_telegram_link_code():
    if not rate_limit("telegram_link_code", 8, 60):
        return jsonify({"success": False, "message": "Too many attempts"}), 429
    data = request.get_json(silent=True) or {}
    hwid = str(data.get("hwid", "")).strip()
    if not hwid or len(hwid) > 256:
        return jsonify({"success": False, "message": "Invalid device id"}), 400
    code = "".join(secrets.choice("ABCDEFGHJKLMNPQRSTUVWXYZ23456789") for _ in range(8))
    created = now_utc()
    expires = created + timedelta(minutes=5)
    with db() as c:
        c.execute("DELETE FROM telegram_link_codes WHERE expires_at<? OR used_at!=''", (iso(created),))
        c.execute("INSERT INTO telegram_link_codes(code_hash,hwid,created_at,expires_at) VALUES(?,?,?,?)",
                  (hash_token(code), hwid, iso(created), iso(expires)))
    return jsonify({"success": True, "link_code": code, "expires_at": iso(expires), "bot_username": TELEGRAM_BOT_USERNAME})


@app.get("/api/v1/telegram/avatar/<int:telegram_id>")
def api_telegram_avatar(telegram_id):
    if not TELEGRAM_BOT_TOKEN:
        abort(404)
    sub = active_subscription(telegram_id)
    if not sub:
        abort(404)
    with db() as c:
        row = c.execute("SELECT photo_file_id FROM telegram_users WHERE telegram_id=?", (telegram_id,)).fetchone()
    if not row or not row["photo_file_id"]:
        abort(404)
    try:
        file_info = telegram_api("getFile", {"file_id": row["photo_file_id"]})
        file_path = file_info.get("file_path")
        if not file_path:
            abort(404)
        url = f"https://api.telegram.org/file/bot{TELEGRAM_BOT_TOKEN}/{file_path}"
        with urlopen(url, timeout=15) as response:
            content = response.read(2 * 1024 * 1024 + 1)
        if len(content) > 2 * 1024 * 1024:
            abort(413)
        return send_file(BytesIO(content), mimetype="image/jpeg", max_age=300)
    except (URLError, HTTPError, RuntimeError):
        abort(404)


@app.post("/api/v1/telegram/login")
def api_telegram_login():
    if not rate_limit("telegram_login", 12, 60):
        return jsonify({"success": False, "message": "Too many attempts"}), 429
    data = request.get_json(silent=True) or {}
    code = str(data.get("link_code", "")).strip().upper()
    hwid = str(data.get("hwid", "")).strip()
    if not code or not hwid or len(hwid) > 256:
        return jsonify({"success": False, "message": "Missing link code or device id"}), 400
    digest = hash_token(code)
    with db() as c:
        link = c.execute("SELECT * FROM telegram_link_codes WHERE code_hash=? AND hwid=?", (digest, hwid)).fetchone()
        if not link or not link["telegram_id"] or link["used_at"] or not parse_iso(link["expires_at"]) or parse_iso(link["expires_at"]) <= now_utc():
            return jsonify({"success": False, "message": "Code is not linked or has expired"}), 403
        telegram_id = int(link["telegram_id"])
        sub = c.execute("""
            SELECT s.id AS subscription_id, s.key_id, s.device_limit,
                   s.expires_at AS subscription_expires_at, s.active AS subscription_active,
                   k.active AS key_active, k.expires_at AS key_expires_at
            FROM subscriptions s JOIN keys k ON k.id=s.key_id
            WHERE s.telegram_id=? AND s.active=1
            ORDER BY s.id DESC LIMIT 1
        """, (telegram_id,)).fetchone()
        if not sub or not sub["subscription_active"] or not sub["key_active"]:
            return jsonify({"success": False, "message": "No active subscription"}), 403
        if sub["subscription_expires_at"] and parse_iso(sub["subscription_expires_at"]) <= now_utc():
            return jsonify({"success": False, "message": "Subscription expired"}), 403
        if sub["key_expires_at"] and parse_iso(sub["key_expires_at"]) <= now_utc():
            return jsonify({"success": False, "message": "Subscription expired"}), 403
        key_id = sub["key_id"]
        device = c.execute("SELECT * FROM devices WHERE key_id=? AND hwid=?", (key_id, hwid)).fetchone()
        count = c.execute("SELECT COUNT(*) n FROM devices WHERE key_id=?", (key_id,)).fetchone()["n"]
        if not device and count >= sub["device_limit"]:
            return jsonify({"success": False, "message": "Device limit reached"}), 403
        now_text = iso(now_utc())
        if device:
            c.execute("UPDATE devices SET last_seen=? WHERE id=?", (now_text, device["id"]))
        else:
            c.execute("INSERT INTO devices(key_id,hwid,first_seen,last_seen,label) VALUES(?,?,?,?,?)",
                      (key_id, hwid, now_text, now_text, f"Telegram:{telegram_id}"))
        c.execute("UPDATE telegram_link_codes SET used_at=? WHERE code_hash=?", (now_text, digest))
        token = secrets.token_urlsafe(48)
        expires = now_utc() + timedelta(minutes=API_SESSION_MINUTES)
        c.execute("INSERT INTO api_sessions(token_hash,key_id,hwid,created_at,expires_at,last_seen) VALUES(?,?,?,?,?,?)",
                  (hash_token(token), key_id, hwid, now_text, iso(expires), now_text))
        profile = c.execute("SELECT username,first_name,last_name FROM telegram_users WHERE telegram_id=?", (telegram_id,)).fetchone()
        key_row = c.execute("SELECT * FROM keys WHERE id=?", (key_id,)).fetchone()
    menu = menu_payload(key_row)
    if not menu["menu_enabled"] or menu["maintenance"]:
        with db() as c:
            c.execute("UPDATE api_sessions SET revoked=1 WHERE token_hash=?", (hash_token(token),))
        return jsonify({"success": False, "message": "MOD MENU unavailable"}), 503
    audit("TELEGRAM_LOGIN_OK", f"telegram_id={telegram_id} hwid={hwid[:16]}...")
    return jsonify({"success": True, "session_token": token, "session_expires_at": iso(expires),
                    "key_expires_at": key_row["expires_at"], "telegram": {"id": telegram_id,
                    "username": profile["username"] if profile else "",
                    "first_name": profile["first_name"] if profile else "",
                    "last_name": profile["last_name"] if profile else "",
                    "avatar_url": f"/api/v1/telegram/avatar/{telegram_id}"},
                    "menu_version": menu["menu_version"], "min_client_version": menu["min_client_version"],
                    "permissions": menu["permissions"], "announcement": menu["announcement"]})


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
              <a href="{{ url_for('devices_page') }}">Devices</a>
              <a href="{{ url_for('features_page') }}">Permissions</a>
              <a href="{{ url_for('telegram_subscriptions_page') }}">Telegram Subscriptions</a>
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

    body = """
    <div class="hero">
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


@app.get("/telegram-subscriptions")
@admin_required
def telegram_subscriptions_page():
    with db() as c:
        requests_rows = c.execute("""
            SELECT r.*, u.username, u.first_name, u.last_name
            FROM subscription_requests r LEFT JOIN telegram_users u ON u.telegram_id=r.telegram_id
            ORDER BY r.id DESC LIMIT 200
        """).fetchall()
        subs_rows = c.execute("""
            SELECT s.*, u.username, u.first_name, k.key,
                   (SELECT COUNT(*) FROM devices d WHERE d.key_id=s.key_id) AS device_count
            FROM subscriptions s LEFT JOIN telegram_users u ON u.telegram_id=s.telegram_id
            JOIN keys k ON k.id=s.key_id ORDER BY s.id DESC LIMIT 200
        """).fetchall()
    body = """
    <h1>Telegram Subscriptions</h1>
    <div class="card">
      <h2>Bot setup</h2>
      <p class="small">Set TELEGRAM_BOT_TOKEN, TELEGRAM_ADMIN_ID and TELEGRAM_POLLING=1 in your hosting environment. Never put the bot token in this page or inside the Android app.</p>
      <p>Bot status: <b class="{{ 'ok' if bot_ready else 'bad' }}">{{ 'Configured' if bot_ready else 'Missing environment variables' }}</b></p>
      <form method="post" action="{{ url_for('telegram_settings_save') }}">
        <input type="hidden" name="csrf" value="{{ csrf }}">
        <label>Payment instructions shown by the bot</label>
        <textarea name="payment_instructions" rows="3">{{ payment_instructions }}</textarea>
        <label>Default device limit for new Telegram subscriptions</label>
        <input type="number" name="device_limit" min="1" max="100" value="{{ device_limit }}">
        <button class="btn primary">Save Telegram Settings</button>
      </form>
    </div>
    <div class="card"><h2>Subscription Requests</h2><table>
      <tr><th>ID</th><th>User</th><th>Plan</th><th>Status</th><th>Payment proof</th><th>Created</th><th>Actions</th></tr>
      {% for r in requests_rows %}<tr>
        <td>#{{ r['id'] }}</td><td>{{ r['first_name'] or '' }} @{{ r['username'] or 'no_username' }}<br><span class="small">{{ r['telegram_id'] }}</span></td>
        <td>{{ plan_labels.get(r['plan'],r['plan']) }}</td><td>{{ r['status'] }}</td><td>{{ r['payment_proof'] or '-' }}</td><td>{{ r['created_at'] }}</td><td>
        {% if r['status']=='pending' %}
          <form class="inline" method="post" action="{{ url_for('telegram_request_action', request_id=r['id'], action='approve') }}"><input type="hidden" name="csrf" value="{{ csrf }}"><button class="btn primary">Approve</button></form>
          <form class="inline" method="post" action="{{ url_for('telegram_request_action', request_id=r['id'], action='reject') }}"><input type="hidden" name="csrf" value="{{ csrf }}"><button class="btn danger">Reject</button></form>
        {% else %}-{% endif %}</td></tr>{% endfor %}
    </table></div>
    <div class="card"><h2>Subscribers</h2><table>
      <tr><th>User</th><th>Plan</th><th>Key</th><th>Devices</th><th>Expires</th><th>Status</th></tr>
      {% for r in subs_rows %}<tr><td>{{ r['first_name'] or '' }} @{{ r['username'] or 'no_username' }}<br><span class="small">{{ r['telegram_id'] }}</span></td>
      <td>{{ plan_labels.get(r['plan'],r['plan']) }}</td><td><code>{{ r['key'] }}</code></td><td>{{ r['device_count'] }}/{{ r['device_limit'] }}</td><td>{{ r['expires_at'] or 'Lifetime' }}</td><td>{{ 'Active' if r['active'] else 'Disabled' }}</td></tr>{% endfor %}
    </table></div>
    """
    return page("Telegram Subscriptions", render_template_string(body, requests_rows=requests_rows,
        subs_rows=subs_rows, plan_labels=PLAN_LABELS, csrf=csrf_token(),
        bot_ready=bool(TELEGRAM_BOT_TOKEN and TELEGRAM_ADMIN_ID),
        payment_instructions=get_setting("payment_instructions", "Please send your payment proof or transaction reference in this chat."),
        device_limit=get_setting("telegram_device_limit", "1")))


@app.post("/telegram-subscriptions/settings")
@admin_required
def telegram_settings_save():
    require_csrf()
    try:
        limit = max(1, min(100, int(request.form.get("device_limit", "1"))))
    except ValueError:
        abort(400, "Invalid device limit")
    set_setting("telegram_device_limit", limit)
    set_setting("payment_instructions", request.form.get("payment_instructions", "")[:2000])
    audit("TELEGRAM_SETTINGS_UPDATED", f"device_limit={limit}")
    return redirect(url_for("telegram_subscriptions_page"))


@app.post("/telegram-subscriptions/<int:request_id>/<action>")
@admin_required
def telegram_request_action(request_id, action):
    require_csrf()
    if action == "approve":
        ok, result = approve_subscription_request(request_id, "web-panel")
        if ok and TELEGRAM_BOT_TOKEN:
            try:
                bot_send(result["telegram_id"], "Your subscription is approved! Open the app, generate a temporary link code, then send it here to connect your account.")
            except Exception as exc:
                audit("TELEGRAM_NOTIFY_ERROR", str(exc)[:500])
    elif action == "reject":
        ok, target = reject_subscription_request(request_id, "web-panel")
        if ok and target and TELEGRAM_BOT_TOKEN:
            try:
                bot_send(target, f"Your subscription request #{request_id} was rejected.")
            except Exception as exc:
                audit("TELEGRAM_NOTIFY_ERROR", str(exc)[:500])
    else:
        abort(404)
    return redirect(url_for("telegram_subscriptions_page"))


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
    start_telegram_polling()
    port = int(os.environ.get("PORT", "8080"))
    # Host must be 0.0.0.0 for Railway/Render/etc.
    app.run(host="0.0.0.0", port=port, debug=False)
