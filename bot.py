import os
import json
import random
import string
import time
import threading
from datetime import datetime, timedelta, timezone

import requests
from flask import Flask, request, jsonify


# =========================================================
# إعدادات
# =========================================================

BOT_TOKEN = os.getenv("8959881524:AAHJKmUz59xbPicuodo-W6prLRg-lJDnbyc", "").strip()

ADMIN_ID = int(
    os.getenv("ADMIN_ID", "8299101176")
)

PORT = int(
    os.getenv("PORT", "8080")
)

TELEGRAM_API = (
    f"https://api.telegram.org/bot{8959881524:AAHJKmUz59xbPicuodo-W6prLRg-lJDnbyc}"
)

BASE_DIR = os.path.dirname(
    os.path.abspath(__file__)
)

KEYS_FILE = os.path.join(
    BASE_DIR,
    "keys.json"
)

session = requests.Session()

states = {}
custom_data = {}

keys_lock = threading.RLock()


# =========================================================
# أدوات الوقت
# =========================================================

def now_utc():
    return datetime.now(timezone.utc)


def parse_datetime(value):
    if not value:
        return None

    try:
        dt = datetime.fromisoformat(value)

        if dt.tzinfo is None:
            dt = dt.replace(
                tzinfo=timezone.utc
            )

        return dt

    except Exception:
        return None


def format_datetime(dt):
    if not dt:
        return "-"

    return dt.strftime(
        "%Y-%m-%d %H:%M:%S UTC"
    )


def is_expired(info):
    expire = parse_datetime(
        info.get("expire")
    )

    if expire is None:
        return True

    return now_utc() >= expire


# =========================================================
# قاعدة البيانات
# =========================================================

def load_keys():

    with keys_lock:

        try:

            if not os.path.exists(
                KEYS_FILE
            ):
                save_keys({})
                return {}

            with open(
                KEYS_FILE,
                "r",
                encoding="utf-8"
            ) as file:

                data = json.load(file)

            if isinstance(data, dict):
                return data

            return {}

        except Exception as e:

            print(
                "LOAD ERROR:",
                repr(e)
            )

            return {}


def save_keys(keys):

    with keys_lock:

        temp_file = (
            KEYS_FILE + ".tmp"
        )

        try:

            with open(
                temp_file,
                "w",
                encoding="utf-8"
            ) as file:

                json.dump(
                    keys,
                    file,
                    ensure_ascii=False,
                    indent=2
                )

            os.replace(
                temp_file,
                KEYS_FILE
            )

        except Exception as e:

            print(
                "SAVE ERROR:",
                repr(e)
            )

            try:
                if os.path.exists(
                    temp_file
                ):
                    os.remove(
                        temp_file
                    )
            except Exception:
                pass


def update_expired_keys():

    with keys_lock:

        keys = load_keys()
        changed = False

        for key, info in keys.items():

            if not isinstance(
                info,
                dict
            ):
                continue

            if is_expired(info):

                if info.get(
                    "active",
                    False
                ):
                    info["active"] = False
                    changed = True

                if not info.get(
                    "expired",
                    False
                ):
                    info["expired"] = True
                    changed = True

        if changed:
            save_keys(keys)

        return keys


# =========================================================
# Telegram API
# =========================================================

def telegram_get(
    method,
    params=None,
    timeout=40
):

    if not BOT_TOKEN:

        print(
            "ERROR: BOT_TOKEN غير موجود."
        )

        return None

    try:

        response = session.get(

            f"{TELEGRAM_API}/{method}",

            params=params or {},

            timeout=timeout
        )

        response.raise_for_status()

        data = response.json()

        if not data.get(
            "ok",
            False
        ):

            print(
                "TELEGRAM ERROR:",
                data
            )

        return data

    except Exception as e:

        print(
            "TELEGRAM GET ERROR:",
            repr(e)
        )

        return None


def telegram_post(
    method,
    data=None,
    timeout=30
):

    if not BOT_TOKEN:

        print(
            "ERROR: BOT_TOKEN غير موجود."
        )

        return None

    try:

        response = session.post(

            f"{TELEGRAM_API}/{method}",

            json=data or {},

            timeout=timeout
        )

        response.raise_for_status()

        result = response.json()

        if not result.get(
            "ok",
            False
        ):

            print(
                "TELEGRAM ERROR:",
                result
            )

        return result

    except Exception as e:

        print(
            "TELEGRAM POST ERROR:",
            repr(e)
        )

        return None


def send_message(
    chat_id,
    text,
    keyboard=None
):

    data = {

        "chat_id": chat_id,

        "text": text
    }

    if keyboard is not None:

        data["reply_markup"] = {

            "keyboard": keyboard,

            "resize_keyboard": True
        }

    return telegram_post(
        "sendMessage",
        data
    )


# =========================================================
# أزرار البوت
# =========================================================

def main_keyboard():

    return [

        [
            {
                "text": "🔑 إنشاء مفتاح"
            },
            {
                "text": "📋 قائمة المفاتيح"
            }
        ],

        [
            {
                "text": "🟢 تفعيل مفتاح"
            },
            {
                "text": "🔴 إيقاف مفتاح"
            }
        ],

        [
            {
                "text": "🗑️ حذف مفتاح"
            },
            {
                "text": "🔎 معلومات مفتاح"
            }
        ]

    ]


def duration_keyboard():

    return [

        [
            {
                "text": "⚡ 1 يوم"
            },
            {
                "text": "📅 7 أيام"
            }
        ],

        [
            {
                "text": "📅 30 يوم"
            },
            {
                "text": "🛠️ مدة مخصصة"
            }
        ],

        [
            {
                "text": "🔙 رجوع"
            }
        ]

    ]


# =========================================================
# إنشاء مفتاح
# =========================================================

def create_key():

    chars = (
        string.ascii_uppercase
        + string.digits
    )

    return (
        "MOLDES-"
        + "".join(
            random.choices(chars, k=4)
        )
        + "-"
        + "".join(
            random.choices(chars, k=4)
        )
        + "-"
        + "".join(
            random.choices(chars, k=4)
        )
    )


def generate_key(
    chat_id,
    days,
    hours,
    minutes
):

    if days < 0:

        send_message(
            chat_id,
            "❌ عدد الأيام غير صحيح."
        )

        return

    if hours < 0 or hours > 23:

        send_message(
            chat_id,
            "❌ الساعات يجب أن تكون من 0 إلى 23."
        )

        return

    if minutes < 0 or minutes > 59:

        send_message(
            chat_id,
            "❌ الدقائق يجب أن تكون من 0 إلى 59."
        )

        return

    if (
        days == 0
        and hours == 0
        and minutes == 0
    ):

        send_message(
            chat_id,
            "❌ المدة يجب أن تكون أكبر من صفر."
        )

        return

    with keys_lock:

        keys = load_keys()

        key = create_key()

        while key in keys:

            key = create_key()

        created = now_utc()

        expire = (
            created
            + timedelta(
                days=days,
                hours=hours,
                minutes=minutes
            )
        )

        keys[key] = {

            "active": True,

            "expired": False,

            "days": days,

            "hours": hours,

            "minutes": minutes,

            "created": created.isoformat(),

            "expire": expire.isoformat()
        }

        save_keys(keys)

    send_message(

        chat_id,

        "✅ تم إنشاء المفتاح\n\n"
        f"🔑 {key}\n\n"
        f"📅 الأيام: {days}\n"
        f"⏰ الساعات: {hours}\n"
        f"⏱️ الدقائق: {minutes}\n\n"
        f"🕐 الانتهاء:\n"
        f"{format_datetime(expire)}",

        main_keyboard()
    )


# =========================================================
# تفعيل مفتاح
# =========================================================

def activate_key(
    chat_id,
    key
):

    with keys_lock:

        keys = load_keys()

        if key not in keys:

            send_message(
                chat_id,
                "❌ المفتاح غير موجود.",
                main_keyboard()
            )

            return

        info = keys[key]

        if is_expired(info):

            info["active"] = False
            info["expired"] = True

            save_keys(keys)

            send_message(
                chat_id,
                "⌛ هذا المفتاح منتهي الصلاحية.",
                main_keyboard()
            )

            return

        info["active"] = True
        info["expired"] = False

        save_keys(keys)

    send_message(
        chat_id,
        "✅ تم تفعيل المفتاح.",
        main_keyboard()
    )


# =========================================================
# إيقاف مفتاح
# =========================================================

def deactivate_key(
    chat_id,
    key
):

    with keys_lock:

        keys = load_keys()

        if key not in keys:

            send_message(
                chat_id,
                "❌ المفتاح غير موجود.",
                main_keyboard()
            )

            return

        keys[key]["active"] = False

        save_keys(keys)

    send_message(
        chat_id,
        "🔴 تم إيقاف المفتاح.",
        main_keyboard()
    )


# =========================================================
# حذف مفتاح
# =========================================================

def delete_key(
    chat_id,
    key
):

    with keys_lock:

        keys = load_keys()

        if key not in keys:

            send_message(
                chat_id,
                "❌ المفتاح غير موجود.",
                main_keyboard()
            )

            return

        del keys[key]

        save_keys(keys)

    send_message(
        chat_id,
        "🗑️ تم حذف المفتاح.",
        main_keyboard()
    )


# =========================================================
# معلومات مفتاح
# =========================================================

def key_info(
    chat_id,
    key
):

    with keys_lock:

        keys = load_keys()

        if key not in keys:

            send_message(
                chat_id,
                "❌ المفتاح غير موجود.",
                main_keyboard()
            )

            return

        info = keys[key]

        if is_expired(info):

            info["active"] = False
            info["expired"] = True

            save_keys(keys)

        if info.get(
            "expired",
            False
        ):

            status = "⌛ منتهي"

        elif info.get(
            "active",
            False
        ):

            status = "🟢 مفعل"

        else:

            status = "🔴 متوقف"

        created = parse_datetime(
            info.get("created")
        )

        expire = parse_datetime(
            info.get("expire")
        )

        text = (

            "🔎 معلومات المفتاح\n\n"

            f"🔑 {key}\n\n"

            f"الحالة: {status}\n\n"

            f"📅 الإنشاء:\n"
            f"{format_datetime(created)}\n\n"

            f"🕐 الانتهاء:\n"
            f"{format_datetime(expire)}"

        )

    send_message(
        chat_id,
        text,
        main_keyboard()
    )


# =========================================================
# قائمة المفاتيح
# =========================================================

def list_keys(chat_id):

    keys = update_expired_keys()

    if not keys:

        send_message(
            chat_id,
            "📋 لا توجد مفاتيح حاليًا.",
            main_keyboard()
        )

        return

    lines = [
        "📋 قائمة المفاتيح",
        ""
    ]

    for key, info in keys.items():

        if info.get(
            "expired",
            False
        ):

            status = "⌛"

        elif info.get(
            "active",
            False
        ):

            status = "🟢"

        else:

            status = "🔴"

        expire = parse_datetime(
            info.get("expire")
        )

        lines.append(
            f"{status} {key}"
        )

        lines.append(
            f"⏰ {format_datetime(expire)}"
        )

        lines.append("")

    text = "\n".join(lines)

    # Telegram يسمح برسائل طويلة،
    # لكن نقسمها احتياطياً.

    max_length = 3500

    if len(text) <= max_length:

        send_message(
            chat_id,
            text,
            main_keyboard()
        )

        return

    for i in range(
        0,
        len(text),
        max_length
    ):

        chunk = text[
            i:i + max_length
        ]

        if (
            i + max_length
            >= len(text)
        ):

            send_message(
                chat_id,
                chunk,
                main_keyboard()
            )

        else:

            send_message(
                chat_id,
                chunk
            )


# =========================================================
# تنظيف الحالة
# =========================================================

def clear_state(chat_id):

    states.pop(
        chat_id,
        None
    )

    custom_data.pop(
        chat_id,
        None
    )


# =========================================================
# معالجة رسائل البوت
# =========================================================

def handle_message(message):

    if not message:
        return

    chat = message.get(
        "chat",
        {}
    )

    sender = message.get(
        "from",
        {}
    )

    chat_id = chat.get(
        "id"
    )

    user_id = sender.get(
        "id"
    )

    if chat_id is None:
        return

    text = message.get(
        "text",
        ""
    ).strip()

    print(
        f"Received: {text!r} "
        f"from {user_id}"
    )

    # =====================================================
    # حماية الأدمن
    # =====================================================

    if user_id != ADMIN_ID:

        send_message(
            chat_id,
            "⛔ ليس لديك صلاحية استخدام هذا البوت."
        )

        return

    # =====================================================
    # /start
    # =====================================================

    if text == "/start":

        clear_state(chat_id)

        send_message(
            chat_id,
            "🔐 Moldes Key Bot\n\n"
            "اختر العملية:",
            main_keyboard()
        )

        return

    # =====================================================
    # رجوع
    # =====================================================

    if text == "🔙 رجوع":

        clear_state(chat_id)

        send_message(
            chat_id,
            "🔐 القائمة الرئيسية:",
            main_keyboard()
        )

        return

    # =====================================================
    # إنشاء مفتاح
    # =====================================================

    if text == "🔑 إنشاء مفتاح":

        states[
            chat_id
        ] = "duration"

        custom_data.pop(
            chat_id,
            None
        )

        send_message(
            chat_id,
            "⏳ اختر مدة المفتاح:",
            duration_keyboard()
        )

        return

    # =====================================================
    # مدة المفتاح
    # =====================================================

    if states.get(
        chat_id
    ) == "duration":

        if text == "⚡ 1 يوم":

            clear_state(chat_id)

            generate_key(
                chat_id,
                1,
                0,
                0
            )

            return

        if text == "📅 7 أيام":

            clear_state(chat_id)

            generate_key(
                chat_id,
                7,
                0,
                0
            )

            return

        if text == "📅 30 يوم":

            clear_state(chat_id)

            generate_key(
                chat_id,
                30,
                0,
                0
            )

            return

        if text == "🛠️ مدة مخصصة":

            states[
                chat_id
            ] = "days"

            custom_data[
                chat_id
            ] = {}

            send_message(
                chat_id,
                "📅 اكتب عدد الأيام:"
            )

            return

        send_message(
            chat_id,
            "❌ اختر مدة من الأزرار.",
            duration_keyboard()
        )

        return

    # =====================================================
    # الأيام
    # =====================================================

    if states.get(
        chat_id
    ) == "days":

        try:

            days = int(text)

            if days < 0:
                raise ValueError

            custom_data.setdefault(
                chat_id,
                {}
            )

            custom_data[
                chat_id
            ]["days"] = days

            states[
                chat_id
            ] = "hours"

            send_message(
                chat_id,
                "⏰ اكتب عدد الساعات:\n"
                "من 0 إلى 23"
            )

        except ValueError:

            send_message(
                chat_id,
                "❌ اكتب رقمًا صحيحًا."
            )

        return

    # =====================================================
    # الساعات
    # =====================================================

    if states.get(
        chat_id
    ) == "hours":

        try:

            hours = int(text)

            if (
                hours < 0
                or hours > 23
            ):
                raise ValueError

            custom_data.setdefault(
                chat_id,
                {}
            )

            custom_data[
                chat_id
            ]["hours"] = hours

            states[
                chat_id
            ] = "minutes"

            send_message(
                chat_id,
                "⏱️ اكتب عدد الدقائق:\n"
                "من 0 إلى 59"
            )

        except ValueError:

            send_message(
                chat_id,
                "❌ الساعات يجب أن تكون من 0 إلى 23."
            )

        return

    # =====================================================
    # الدقائق
    # =====================================================

    if states.get(
        chat_id
    ) == "minutes":

        try:

            minutes = int(text)

            if (
                minutes < 0
                or minutes > 59
            ):
                raise ValueError

            data = custom_data.get(
                chat_id,
                {}
            )

            days = int(
                data.get(
                    "days",
                    0
                )
            )

            hours = int(
                data.get(
                    "hours",
                    0
                )
            )

            if (
                days == 0
                and hours == 0
                and minutes == 0
            ):

                send_message(
                    chat_id,
                    "❌ المدة يجب أن تكون أكبر من صفر."
                )

                return

            clear_state(chat_id)

            generate_key(
                chat_id,
                days,
                hours,
                minutes
            )

        except ValueError:

            send_message(
                chat_id,
                "❌ الدقائق يجب أن تكون من 0 إلى 59."
            )

        return

    # =====================================================
    # تفعيل
    # =====================================================

    if text == "🟢 تفعيل مفتاح":

        states[
            chat_id
        ] = "activate"

        send_message(
            chat_id,
            "🟢 أرسل المفتاح الذي تريد تفعيله."
        )

        return

    if states.get(
        chat_id
    ) == "activate":

        clear_state(chat_id)

        activate_key(
            chat_id,
            text
        )

        return

    # =====================================================
    # إيقاف
    # =====================================================

    if text == "🔴 إيقاف مفتاح":

        states[
            chat_id
        ] = "deactivate"

        send_message(
            chat_id,
            "🔴 أرسل المفتاح الذي تريد إيقافه."
        )

        return

    if states.get(
        chat_id
    ) == "deactivate":

        clear_state(chat_id)

        deactivate_key(
            chat_id,
            text
        )

        return

    # =====================================================
    # حذف
    # =====================================================

    if text == "🗑️ حذف مفتاح":

        states[
            chat_id
        ] = "delete"

        send_message(
            chat_id,
            "🗑️ أرسل المفتاح الذي تريد حذفه."
        )

        return

    if states.get(
        chat_id
    ) == "delete":

        clear_state(chat_id)

        delete_key(
            chat_id,
            text
        )

        return

    # =====================================================
    # معلومات
    # =====================================================

    if text == "🔎 معلومات مفتاح":

        states[
            chat_id
        ] = "info"

        send_message(
            chat_id,
            "🔎 أرسل المفتاح."
        )

        return

    if states.get(
        chat_id
    ) == "info":

        clear_state(chat_id)

        key_info(
            chat_id,
            text
        )

        return

    # =====================================================
    # القائمة
    # =====================================================

    if text == "📋 قائمة المفاتيح":

        clear_state(chat_id)

        list_keys(
            chat_id
        )

        return

    # =====================================================
    # أمر غير معروف
    # =====================================================

    send_message(
        chat_id,
        "❓ اختر أحد الأزرار.",
        main_keyboard()
    )


# =========================================================
# Polling
# =========================================================

def run_bot():

    if not BOT_TOKEN:

        print(
            "========================================"
        )

        print(
            "ERROR: BOT_TOKEN غير موجود!"
        )

        print(
            "أضف BOT_TOKEN في Railway Variables."
        )

        print(
            "========================================"
        )

        return

    print(
        "========================================"
    )

    print(
        "Moldes Key Bot Started"
    )

    print(
        f"Admin ID: {ADMIN_ID}"
    )

    print(
        "Telegram Polling: ON"
    )

    print(
        "========================================"
    )

    # =====================================================
    # اختبار الاتصال
    # =====================================================

    me = telegram_get(
        "getMe"
    )

    if me and me.get(
        "ok"
    ):

        bot = me[
            "result"
        ]

        print(
            "Bot connected:",
            bot.get(
                "username",
                "unknown"
            )
        )

    else:

        print(
            "ERROR: فشل الاتصال بـ Telegram."
        )

        return

    # =====================================================
    # حذف Webhook إن وجد
    # =====================================================

    delete_result = telegram_post(
        "deleteWebhook",
        {
            "drop_pending_updates": False
        }
    )

    if delete_result and delete_result.get(
        "ok"
    ):

        print(
            "Webhook cleared."
        )

    # =====================================================
    # Polling
    # =====================================================

    offset = None

    while True:

        try:

            update_expired_keys()

            params = {
                "timeout": 30
            }

            if offset is not None:

                params[
                    "offset"
                ] = offset

            data = telegram_get(
                "getUpdates",
                params,
                timeout=40
            )

            if not data:

                time.sleep(3)

                continue

            if not data.get(
                "ok",
                False
            ):

                print(
                    "Telegram API error:",
                    data
                )

                time.sleep(5)

                continue

            updates = data.get(
                "result",
                []
            )

            for update in updates:

                update_id = update.get(
                    "update_id"
                )

                if update_id is not None:

                    offset = (
                        update_id + 1
                    )

                message = update.get(
                    "message"
                )

                if message:

                    try:

                        handle_message(
                            message
                        )

                    except Exception as e:

                        print(
                            "MESSAGE ERROR:",
                            repr(e)
                        )

        except Exception as e:

            print(
                "POLLING ERROR:",
                repr(e)
            )

            time.sleep(5)


# =========================================================
# Flask API
# =========================================================

app = Flask(
    __name__
)


@app.route(
    "/",
    methods=["GET"]
)
def home():

    return jsonify({
        "success": True,
        "service": "Moldes Key API",
        "status": "online"
    })


@app.route(
    "/health",
    methods=["GET"]
)
def health():

    return jsonify({
        "success": True,
        "status": "ok"
    })


@app.route(
    "/check",
    methods=["GET", "POST"]
)
@app.route(
    "/check.php",
    methods=["GET", "POST"]
)
def check_key():

    try:

        update_expired_keys()

        key = request.form.get(
            "key",
            ""
        ).strip()

        if not key:

            key = request.args.get(
                "key",
                ""
            ).strip()

        if (
            not key
            and request.is_json
        ):

            body = request.get_json(
                silent=True
            ) or {}

            key = str(
                body.get(
                    "key",
                    ""
                )
            ).strip()

        if not key:

            return jsonify({

                "success": False,

                "message": "No key provided"

            }), 400

        with keys_lock:

            keys = load_keys()

            if key not in keys:

                return jsonify({

                    "success": False,

                    "message": "Key not found"

                })

            info = keys[key]

            if is_expired(info):

                info["active"] = False

                info["expired"] = True

                save_keys(keys)

                return jsonify({

                    "success": False,

                    "message": "Key expired"

                })

            if (
                info.get(
                    "active",
                    False
                )
                and not info.get(
                    "expired",
                    False
                )
            ):

                return jsonify({

                    "success": True,

                    "message": "Key is valid"

                })

            return jsonify({

                "success": False,

                "message": "Key is inactive"

            })

    except Exception as e:

        print(
            "API ERROR:",
            repr(e)
        )

        return jsonify({

            "success": False,

            "message": "Internal server error"

        }), 500


# =========================================================
# تشغيل Flask + Telegram
# =========================================================

def run_web_server():

    print(
        f"Flask running on port {PORT}"
    )

    app.run(

        host="0.0.0.0",

        port=PORT,

        debug=False,

        use_reloader=False

    )


if __name__ == "__main__":

    # تشغيل Flask في Thread
    web_thread = threading.Thread(

        target=run_web_server,

        daemon=True

    )

    web_thread.start()

    # تشغيل Telegram
    run_bot()
