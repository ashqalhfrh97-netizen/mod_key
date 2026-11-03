

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
ADMIN_PASS = os.environ.get("ADMIN_PASS", "").strip()
SECRET_KEY = os.environ.get("SECRET_KEY", "").strip()

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
PLAN_LABELS = {"week": "أسبوع واحد", "month": "شهر واحد", "year": "سنة واحدة", "lifetime": "اشتراك دائم"}


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


def bot_send_photo(chat_id, photo, caption="", reply_markup=None):
    """Forward a Telegram photo file_id to the admin with optional review buttons."""
    payload = {"chat_id": int(chat_id), "photo": photo, "caption": caption[:1024]}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    return telegram_api("sendPhoto", payload)


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
    """Store and return the largest available Telegram profile-photo file_id."""
    if not TELEGRAM_BOT_TOKEN:
        return ""
    try:
        result = telegram_api(
            "getUserProfilePhotos",
            {"user_id": int(telegram_id), "offset": 0, "limit": 1}
        )
        photos = result.get("photos") or []
        file_id = photos[0][-1].get("file_id", "") if photos and photos[0] else ""
        with db() as c:
            c.execute(
                "UPDATE telegram_users SET photo_file_id=? WHERE telegram_id=?",
                (file_id, int(telegram_id))
            )
        return file_id
    except Exception as exc:
        audit("TELEGRAM_PHOTO_ERROR", str(exc)[:300])
        return ""


def notify_admin_new_subscription_request(request_id, user, plan):
    """Send the applicant's Telegram profile photo and request details to the admin."""
    if not TELEGRAM_ADMIN_ID:
        return

    username = user.get("username") or "بدون اسم مستخدم"
    full_name = " ".join(
        part for part in (user.get("first_name", ""), user.get("last_name", "")) if part
    ) or "غير متوفر"
    caption = (
        f"طلب اشتراك جديد #{request_id}\n"
        f"الاسم: {full_name}\n"
        f"المستخدم: @{username}\n"
        f"معرّف تيليجرام: {user.get('id')}\n"
        f"الخطة: {PLAN_LABELS.get(plan, plan)}\n"
        "هذه صورة الملف الشخصي في تيليجرام. بانتظار صورة حساب اللعبة."
    )

    with db() as c:
        row = c.execute(
            "SELECT photo_file_id FROM telegram_users WHERE telegram_id=?",
            (int(user["id"]),)
        ).fetchone()
    profile_photo = row["photo_file_id"] if row else ""

    try:
        if profile_photo:
            bot_send_photo(TELEGRAM_ADMIN_ID, profile_photo, caption)
        else:
            bot_send(
                TELEGRAM_ADMIN_ID,
                caption + "\n\nلم يضع المستخدم صورة شخصية في تيليجرام أو تعذر جلبها."
            )
    except Exception as exc:
        audit("TELEGRAM_PROFILE_FORWARD_ERROR", str(exc)[:500])
        try:
            bot_send(TELEGRAM_ADMIN_ID, caption + "\n\nتعذر إرسال الصورة الشخصية.")
        except Exception as notify_exc:
            audit("TELEGRAM_ADMIN_NOTIFY_ERROR", str(notify_exc)[:500])


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
            return False, "تمت مراجعة هذا الطلب مسبقاً"
        if not (req["payment_proof"] or "").startswith("photo_file_id:"):
            return False, "لا يمكن قبول الطلب قبل استلام صورة حساب اللعبة"
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
                telegram_api("answerCallbackQuery", {"callback_query_id": callback_id, "text": "خطة غير صحيحة"})
                return
            with db() as c:
                cur = c.execute("""
                    INSERT INTO subscription_requests(telegram_id,plan,status,created_at)
                    VALUES(?,?,'pending',?)
                """, (int(user["id"]), plan, iso(now_utc())))
                request_id = cur.lastrowid
            telegram_api("answerCallbackQuery", {"callback_query_id": callback_id, "text": "تم إنشاء الطلب"})
            instructions = get_setting("payment_instructions", "").strip()
            extra = f"\n\nملاحظة: {instructions}" if instructions else ""
            bot_send(user["id"],
                f"تم إنشاء طلب الاشتراك رقم #{request_id} ({PLAN_LABELS[plan]}).\n\n"
                "الخطوة التالية: أرسل صورة واضحة من داخل لعبة Bounty Rush يظهر فيها حسابك أو معلومات اللاعب. "
                "يمكنك إضافة رقم اللاعب أو اسمه في وصف الصورة.\n\n"
                "لا ترسل كلمة مرور أو رمز تسجيل دخول." + extra)
            notify_admin_new_subscription_request(request_id, user, plan)
            return
        if data.startswith("req:"):
            if not TELEGRAM_ADMIN_ID or str(user.get("id")) != TELEGRAM_ADMIN_ID:
                telegram_api("answerCallbackQuery", {"callback_query_id": callback_id, "text": "غير مصرح لك", "show_alert": True})
                return
            try:
                _, action, raw_id = data.split(":", 2)
                request_id = int(raw_id)
            except (ValueError, TypeError):
                return
            if action == "approve":
                ok, result = approve_subscription_request(request_id, str(user.get("id")))
                if ok:
                    expiry = result["expires_at"] or "دائم"
                    bot_send(result["telegram_id"],
                        f"تمت الموافقة على اشتراكك!\nانتهى الاشتراك: {expiry}\n\n"
                        "افتح التطبيق وأنشئ رمز ربط مؤقت، ثم أرسل الرمز هنا لربط حسابك.")
                    telegram_api("answerCallbackQuery", {"callback_query_id": callback_id, "text": "تمت الموافقة"})
                    bot_send(user["id"], f"تم قبول الطلب رقم #{request_id}. عدد الأجهزة المسموح: {result['device_limit']}.")
                else:
                    telegram_api("answerCallbackQuery", {"callback_query_id": callback_id, "text": str(result), "show_alert": True})
            elif action == "reject":
                ok, target = reject_subscription_request(request_id, str(user.get("id")))
                if ok and target:
                    bot_send(target, f"لم تتم الموافقة على طلب الاشتراك رقم #{request_id}. يمكنك التواصل مع الإدارة للاستفسار.")
                    telegram_api("answerCallbackQuery", {"callback_query_id": callback_id, "text": "تم رفض الطلب"})
                else:
                    telegram_api("answerCallbackQuery", {"callback_query_id": callback_id, "text": "تمت مراجعة الطلب مسبقاً", "show_alert": True})
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

    # Owner-only instructions.
    if text_value.split(" ", 1)[0].split("@", 1)[0] == "/adminhelp":
        if not TELEGRAM_ADMIN_ID or str(user.get("id")) != TELEGRAM_ADMIN_ID:
            bot_send(chat_id, "هذا الأمر مخصص للإدارة فقط.")
            return
        bot_send(chat_id,
            "تعليمات مراجعة طلبات الاشتراك:\n\n"
            "1) اختر الطلب من رسالة البوت التي تصلك عند إنشاء اشتراك جديد.\n"
            "2) انتظر وصول صورة حساب Bounty Rush؛ ستصلك الصورة نفسها مع رقم الطلب وبيانات صاحبه.\n"
            "3) افحص أن الصورة واضحة وتُظهر معلومات الحساب المطلوبة.\n"
            "4) اضغط (موافقة) لتفعيل الاشتراك تلقائياً، أو (رفض) إذا كانت الصورة غير واضحة أو لا تخص الحساب.\n"
            "5) يمكن أيضاً مراجعة الطلبات من لوحة التحكم ثم صفحة اشتراكات تيليجرام.\n\n"
            "مهم: لا تطلب كلمة مرور اللعبة أو رموز تسجيل الدخول من أي شخص. راجع الصور ومعلومات الحساب الظاهرة فقط.")
        return

    if text_value.startswith("/start"):
        markup = {"inline_keyboard": [
            [{"text": "أسبوع واحد", "callback_data": "plan:week"}, {"text": "شهر واحد", "callback_data": "plan:month"}],
            [{"text": "سنة واحدة", "callback_data": "plan:year"}, {"text": "اشتراك دائم", "callback_data": "plan:lifetime"}]
        ]}
        bot_send(
            chat_id,
            "أهلاً بك! اختر مدة الاشتراك:\n\n"
            "عند تقديم الطلب، سيتم إرسال صورة ملفك الشخصي المتاحة في تيليجرام إلى الإدارة للمراجعة، "
            "وبعدها سيُطلب منك إرسال صورة حسابك من داخل لعبة Bounty Rush.\n"
            "لا ترسل كلمة مرور أو رمز تسجيل الدخول.",
            markup
        )
        return

    # A photo from the game account is the only accepted subscription proof.
    if message.get("photo"):
        with db() as c:
            req = c.execute("""
                SELECT * FROM subscription_requests
                WHERE telegram_id=? AND status='pending'
                ORDER BY id DESC LIMIT 1
            """, (int(user["id"]),)).fetchone()
            if req:
                file_id = message["photo"][-1].get("file_id", "")
                caption_text = (message.get("caption") or "").strip()
                proof_value = "photo_file_id:" + file_id + "\ncaption:" + caption_text
                c.execute("UPDATE subscription_requests SET payment_proof=? WHERE id=?", (proof_value[:1500], req["id"]))
        if not req:
            bot_send(chat_id, "لم أجد طلب اشتراك معلّقاً. أرسل /start واختر مدة الاشتراك أولاً.")
            return
        if not file_id:
            bot_send(chat_id, "لم أتمكن من قراءة الصورة. أعد إرسال لقطة الشاشة من فضلك.")
            return
        if TELEGRAM_ADMIN_ID:
            caption = (
                f"صورة حساب لطلب الاشتراك #{req['id']}\n"
                f"المستخدم: {user.get('first_name','')} (@{user.get('username','')})\n"
                f"معرّف تيليجرام: {user['id']}\n"
                f"الخطة: {PLAN_LABELS.get(req['plan'], req['plan'])}\n"
                f"معلومات إضافية: {caption_text or 'لا يوجد'}"
            )
            markup = {"inline_keyboard": [[
                {"text": "موافقة", "callback_data": f"req:approve:{req['id']}"},
                {"text": "رفض", "callback_data": f"req:reject:{req['id']}"}
            ]]}
            try:
                bot_send_photo(TELEGRAM_ADMIN_ID, file_id, caption, markup)
            except Exception as exc:
                audit("TELEGRAM_PROOF_FORWARD_ERROR", str(exc)[:500])
                bot_send(chat_id, "تم استلام الصورة، لكن تعذر إرسالها للإدارة حالياً. حاول مرة أخرى بعد قليل.")
                return
        bot_send(chat_id, "وصلت صورة الحساب وتم إرسالها إلى الإدارة للمراجعة. انتظر الرد هنا.")
        return

    # Text is not accepted as proof; users must send an in-game screenshot.
    if text_value:
        with db() as c:
            pending = c.execute("""
                SELECT id FROM subscription_requests
                WHERE telegram_id=? AND status='pending'
                ORDER BY id DESC LIMIT 1
            """, (int(user["id"]),)).fetchone()
        if pending and not text_value.startswith("/"):
            bot_send(chat_id, "لا نعتمد النصوص أو أرقام التحويل كإثبات. أرسل صورة واضحة لحسابك من داخل لعبة Bounty Rush، ويمكنك كتابة رقم اللاعب في وصف الصورة.")
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
            bot_send(chat_id, "تم ربط الحساب بنجاح! ارجع إلى التطبيق لإكمال تسجيل الدخول.")
        else:
            bot_send(chat_id, "الرمز غير صالح. تأكد من تفعيل اشتراكك وأن الرمز ما زال صالحاً.")


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
.hero{overflow:hidden;position:relative;border:1px solid var(--line);border-radius:20px;background:#0c1421;margin-bottom:18px;box-shadow:0 18px 55px rgba(0,0,0,.28)}.hero-overlay{position:absolute;inset:0;padding:28px;display:flex;align-items:flex-end;background:linear-gradient(90deg,rgba(5,9,15,.92),rgba(5,9,15,.25) 70%,rgba(5,9,15,.65))}.hero h1{font-size:clamp(26px,4vw,44px);margin:0 0 8px}.hero p{margin:0;color:#b8c7
