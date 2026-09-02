import requests
import json
import os
import random
import string
import time
import threading
from datetime import datetime, timedelta, timezone
from flask import Flask, request, jsonify


# =========================================================
# إعدادات البوت
# =========================================================

# ضع التوكن في Environment Variables
# BOT_TOKEN=ضع_التوكن_الجديد_هنا
BOT_TOKEN = os.environ.get("8959881524:AAHJKmUz59xbPicuodo-W6prLRg-lJDnbyc", "").strip()

# ضع ID الأدمن في Environment Variables
# ADMIN_ID=8299101176
try:
    ADMIN_ID = int(os.environ.get("ADMIN_ID", "8299101176"))
except ValueError:
    ADMIN_ID = 8299101176


# رابط API
TELEGRAM_API = os.environ.get(
    "TELEGRAM_API",
    "https://modkey-production-0d27.up.railway.app"
).rstrip("/")


# =========================================================
# إعدادات الملفات
# =========================================================

BASE_DIR = os.path.dirname(
    os.path.abspath(__file__)
)

KEYS_FILE = os.path.join(
    BASE_DIR,
    "keys.json"
)


# =========================================================
# إعدادات عامة
# =========================================================

HTTP_TIMEOUT = 30

states = {}
custom_data = {}

# منع تعارض قراءة وكتابة قاعدة البيانات
keys_lock = threading.RLock()

# Session واحدة للطلبات
session = requests.Session()


# =========================================================
# التحقق من الإعدادات
# =========================================================

if not BOT_TOKEN:
    print("WARNING: BOT_TOKEN غير موجود.")
    print("أضف BOT_TOKEN في Environment Variables.")


# =========================================================
# الوقت
# =========================================================

def get_now():
    """
    الحصول على الوقت الحالي بصيغة UTC.
    """
    return datetime.now(timezone.utc)


def format_datetime(dt):
    """
    تحويل التاريخ إلى نص واضح.
    """
    if not dt:
        return "-"

    return dt.strftime(
        "%Y-%m-%d %H:%M:%S UTC"
    )


def parse_datetime(value):
    """
    قراءة التاريخ من JSON.

    يدعم:
    - ISO format
    - الصيغة القديمة:
      YYYY-MM-DD HH:MM:SS
    """

    if not value:
        return None

    # الصيغة الجديدة
    try:
        dt = datetime.fromisoformat(
            value
        )

        if dt.tzinfo is None:
            dt = dt.replace(
                tzinfo=timezone.utc
            )

        return dt

    except Exception:
        pass

    # الصيغة القديمة
    try:
        dt = datetime.strptime(
            value,
            "%Y-%m-%d %H:%M:%S"
        )

        return dt.replace(
            tzinfo=timezone.utc
        )

    except Exception:
        return None


# =========================================================
# قاعدة البيانات
# =========================================================

def save_keys(keys):
    """
    حفظ المفاتيح في keys.json.

    يتم استخدام ملف مؤقت لتقليل احتمالية
    تلف الملف إذا توقف البرنامج أثناء الحفظ.
    """

    temp_file = KEYS_FILE + ".tmp"

    try:

        with open(
            temp_file,
            "w",
            encoding="utf-8"
        ) as f:

            json.dump(
                keys,
                f,
                ensure_ascii=False,
                indent=2
            )

            f.flush()

            try:
                os.fsync(
                    f.fileno()
                )
            except Exception:
                pass

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


def load_keys():
    """
    تحميل المفاتيح من keys.json.
    """

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
            ) as f:

                data = json.load(f)

            if not isinstance(
                data,
                dict
            ):
                print(
                    "WARNING: keys.json غير صحيح."
                )
                return {}

            return data

        except json.JSONDecodeError as e:

            print(
                "JSON ERROR:",
                repr(e)
            )

            return {}

        except Exception as e:

            print(
                "LOAD ERROR:",
                repr(e)
            )

            return {}


# =========================================================
# فحص انتهاء المفتاح
# =========================================================

def is_expired(info):
    """
    التحقق من انتهاء المفتاح.
    """

    if not isinstance(
        info,
        dict
    ):
        return True

    expire = parse_datetime(
        info.get("expire")
    )

    if expire is None:
        return True

    return get_now() >= expire


def normalize_key_info(info):
    """
    التأكد من وجود البيانات الأساسية.
    """

    if not isinstance(
        info,
        dict
    ):
        return {
            "active": False,
            "expired": True
        }

    info.setdefault(
        "active",
        False
    )

    info.setdefault(
        "expired",
        False
    )

    return info


def update_expired_keys():
    """
    تحديث جميع المفاتيح المنتهية.
    """

    with keys_lock:

        keys = load_keys()

        changed = False

        for key, raw_info in keys.items():

            info = normalize_key_info(
                raw_info
            )

            if is_expired(info):

                if info.get(
                    "active"
                ) is not False:

                    info["active"] = False
                    changed = True

                if info.get(
                    "expired"
                ) is not True:

                    info["expired"] = True
                    changed = True

        if changed:
            save_keys(keys)

        return keys


# =========================================================
# إنشاء المفاتيح
# =========================================================

def create_key():

    chars = (
        string.ascii_uppercase
        + string.digits
    )

    return (
        "MOLDES-"
        + "".join(
            random.choices(
                chars,
                k=4
            )
        )
        + "-"
        + "".join(
            random.choices(
                chars,
                k=4
            )
        )
        + "-"
        + "".join(
            random.choices(
                chars,
                k=4
            )
        )
    )


def make_expire(
    days,
    hours,
    minutes
):

    expire_time = (
        get_now()
        + timedelta(
            days=days,
            hours=hours,
            minutes=minutes
        )
    )

    return expire_time.isoformat()


def generate_key(
    chat_id,
    days,
    hours,
    minutes
):

    # التحقق من المدة

    if days < 0:

        send_message(
            chat_id,
            "❌ عدد الأيام لا يمكن أن يكون سالبًا."
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
            "❌ مدة المفتاح يجب أن تكون أكبر من صفر."
        )

        return

    with keys_lock:

        keys = load_keys()

        key = create_key()

        while key in keys:
            key = create_key()

        created = get_now()

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
# Telegram API
# =========================================================

def telegram_post(
    method,
    data=None,
    timeout=HTTP_TIMEOUT
):

    url = (
        f"{TELEGRAM_API}/{method}"
    )

    try:

        response = session.post(
            url,
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
                "TELEGRAM API ERROR:",
                result
            )

        return result

    except requests.RequestException as e:

        print(
            "TELEGRAM HTTP ERROR:",
            repr(e)
        )

    except ValueError as e:

        print(
            "TELEGRAM JSON ERROR:",
            repr(e)
        )

    except Exception as e:

        print(
            "TELEGRAM ERROR:",
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

            "resize_keyboard": True,

            "one_time_keyboard": False
        }

    return telegram_post(
        "sendMessage",
        data
    )


def get_updates(offset=None):

    url = (
        f"{TELEGRAM_API}/getUpdates"
    )

    params = {
        "timeout": 30
    }

    if offset is not None:

        params["offset"] = offset

    try:

        response = session.get(
            url,
            params=params,
            timeout=40
        )

        response.raise_for_status()

        return response.json()

    except requests.RequestException as e:

        print(
            "GET UPDATES HTTP ERROR:",
            repr(e)
        )

    except ValueError as e:

        print(
            "GET UPDATES JSON ERROR:",
            repr(e)
        )

    except Exception as e:

        print(
            "GET UPDATES ERROR:",
            repr(e)
        )

    return None


# =========================================================
# Telegram Keyboards
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
# إدارة الحالات
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

        info = normalize_key_info(
            keys[key]
        )

        # لا يمكن تفعيل مفتاح منتهي

        if is_expired(info):

            info["active"] = False

            info["expired"] = True

            save_keys(keys)

            send_message(
                chat_id,
                "⌛ لا يمكن تفعيل مفتاح منتهي الصلاحية.",
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

def key_information(
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

        info = normalize_key_info(
            keys[key]
        )

        # تحديث حالة انتهاء الصلاحية

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

        created_text = (
            format_datetime(created)
        )

        expire_text = (
            format_datetime(expire)
        )

        text = (

            "🔎 معلومات المفتاح\n\n"

            f"🔑 {key}\n\n"

            f"الحالة: {status}\n\n"

            f"📅 تاريخ الإنشاء:\n"
            f"{created_text}\n\n"

            f"🕐 تاريخ الانتهاء:\n"
            f"{expire_text}"

        )

    send_message(
        chat_id,
        text,
        main_keyboard()
    )


# =========================================================
# إرسال رسائل طويلة
# =========================================================

def send_long_message(
    chat_id,
    text,
    keyboard=None,
    max_length=3500
):

    if len(text) <= max_length:

        send_message(
            chat_id,
            text,
            keyboard
        )

        return

    chunks = []

    current = ""

    for line in text.splitlines(
        keepends=True
    ):

        if (
            len(current)
            + len(line)
            > max_length
        ):

            if current:
                chunks.append(
                    current
                )

            current = line

        else:

            current += line

    if current:
        chunks.append(current)

    for index, chunk in enumerate(
        chunks
    ):

        if index == len(chunks) - 1:

            send_message(
                chat_id,
                chunk,
                keyboard
            )

        else:

            send_message(
                chat_id,
                chunk
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

    for key, raw_info in keys.items():

        info = normalize_key_info(
            raw_info
        )

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

        expire_text = (
            format_datetime(expire)
        )

        lines.append(
            f"{status} {key}"
        )

        lines.append(
            f"⏰ {expire_text}"
        )

        lines.append("")

    send_long_message(
        chat_id,
        "\n".join(lines),
        main_keyboard()
    )


# =========================================================
# Flask Web API
# =========================================================

app = Flask(__name__)


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
        "status": "ok"
    })


@app.route(
    "/check.php",
    methods=["GET", "POST"]
)
@app.route(
    "/check",
    methods=["GET", "POST"]
)
def api_check_key():

    try:

        # تحديث المفاتيح المنتهية

        update_expired_keys()

        # -------------------------------------
        # POST Form
        # -------------------------------------

        key = request.form.get(
            "key",
            ""
        ).strip()

        # -------------------------------------
        # GET
        # -------------------------------------

        if not key:

            key = request.args.get(
                "key",
                ""
            ).strip()

        # -------------------------------------
        # JSON
        # -------------------------------------

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

        # -------------------------------------
        # لا يوجد مفتاح
        # -------------------------------------

        if not key:

            return jsonify({

                "success": False,

                "message": "No key provided"

            }), 400

        # -------------------------------------
        # البحث عن المفتاح
        # -------------------------------------

        with keys_lock:

            keys = load_keys()

            if key not in keys:

                return jsonify({

                    "success": False,

                    "message": "Key not found"

                })

            info = normalize_key_info(
                keys[key]
            )

            # ---------------------------------
            # انتهاء المفتاح
            # ---------------------------------

            if is_expired(info):

                info["active"] = False

                info["expired"] = True

                save_keys(keys)

                return jsonify({

                    "success": False,

                    "message": "Key expired"

                })

            # ---------------------------------
            # المفتاح فعال
            # ---------------------------------

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

            # ---------------------------------
            # المفتاح متوقف
            # ---------------------------------

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
# معالجة رسائل Telegram
# =========================================================

def handle_message(message):

    if not message:
        return

    chat = message.get(
        "chat",
        {}
    )

    user = message.get(
        "from",
        {}
    )

    chat_id = chat.get(
        "id"
    )

    user_id = user.get(
        "id"
    )

    if (
        chat_id is None
        or user_id is None
    ):
        return

    text = message.get(
        "text",
        ""
    ).strip()

    print(
        "Received:",
        repr(text),
        "from:",
        user_id
    )

    # =====================================================
    # التحقق من الأدمن
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

        clear_state(
            chat_id
        )

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

        clear_state(
            chat_id
        )

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

        states[chat_id] = "duration"

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
    # اختيار المدة
    # =====================================================

    if states.get(
        chat_id
    ) == "duration":

        if text == "⚡ 1 يوم":

            clear_state(
                chat_id
            )

            generate_key(
                chat_id,
                1,
                0,
                0
            )

            return

        if text == "📅 7 أيام":

            clear_state(
                chat_id
            )

            generate_key(
                chat_id,
                7,
                0,
                0
            )

            return

        if text == "📅 30 يوم":

            clear_state(
                chat_id
            )

            generate_key(
                chat_id,
                30,
                0,
                0
            )

            return

        if text == "🛠️ مدة مخصصة":

            states[chat_id] = "days"

            custom_data[chat_id] = {}

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

        except (
            ValueError,
            TypeError
        ):

            send_message(
                chat_id,
                "❌ اكتب رقمًا صحيحًا للأيام."
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

        except (
            ValueError,
            TypeError
        ):

            send_message(

                chat_id,

                "❌ الساعات يجب أن تكون "
                "من 0 إلى 23."

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

                    "❌ المدة يجب أن تكون "
                    "أكبر من صفر."

                )

                return

            clear_state(
                chat_id
            )

            generate_key(

                chat_id,

                days,

                hours,

                minutes

            )

        except (
            ValueError,
            TypeError
        ):

            send_message(

                chat_id,

                "❌ الدقائق يجب أن تكون "
                "من 0 إلى 59."

            )

        return

    # =====================================================
    # تفعيل مفتاح
    # =====================================================

    if text == "🟢 تفعيل مفتاح":

        states[
            chat_id
        ] = "activate"

        send_message(

            chat_id,

            "🟢 أرسل المفتاح "
            "الذي تريد تفعيله."

        )

        return

    if states.get(
        chat_id
    ) == "activate":

        clear_state(
            chat_id
        )

        activate_key(
            chat_id,
            text
        )

        return

    # =====================================================
    # إيقاف مفتاح
    # =====================================================

    if text == "🔴 إيقاف مفتاح":

        states[
            chat_id
        ] = "deactivate"

        send_message(

            chat_id,

            "🔴 أرسل المفتاح "
            "الذي تريد إيقافه."

        )

        return

    if states.get(
        chat_id
    ) == "deactivate":

        clear_state(
            chat_id
        )

        deactivate_key(
            chat_id,
            text
        )

        return

    # =====================================================
    # حذف مفتاح
    # =====================================================

    if text == "🗑️ حذف مفتاح":

        states[
            chat_id
        ] = "delete"

        send_message(

            chat_id,

            "🗑️ أرسل المفتاح "
            "الذي تريد حذفه."

        )

        return

    if states.get(
        chat_id
    ) == "delete":

        clear_state(
            chat_id
        )

        delete_key(
            chat_id,
            text
        )

        return

    # =====================================================
    # معلومات مفتاح
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

        clear_state(
            chat_id
        )

        key_information(
            chat_id,
            text
        )

        return

    # =====================================================
    # قائمة المفاتيح
    # =====================================================

    if text == "📋 قائمة المفاتيح":

        clear_state(
            chat_id
        )

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
# تشغيل البوت
# =========================================================

def main():

    offset = None

    print(
        "===================================="
    )

    print(
        "Moldes Key Bot Started"
    )

    print(
        "Telegram API:",
        TELEGRAM_API
    )

    print(
        "Admin ID:",
        ADMIN_ID
    )

    print(
        "===================================="
    )

    while True:

        try:

            # تحديث المفاتيح المنتهية

            update_expired_keys()

            # الحصول على تحديثات Telegram

            data = get_updates(
                offset
            )

            if not data:

                time.sleep(3)

                continue

            if not data.get(
                "ok",
                False
            ):

                print(
                    "Telegram error:",
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

                if not message:
                    continue

                try:

                    handle_message(
                        message
                    )

                except Exception as e:

                    print(
                        "MESSAGE ERROR:",
                        repr(e)
                    )

        except KeyboardInterrupt:

            print(
                "Bot stopped."
            )

            break

        except Exception as e:

            print(
                "MAIN ERROR:",
                repr(e)
            )

            time.sleep(5)


# =========================================================
# Flask Server
# =========================================================

def run_web_server():

    print(
        f"Web server starting on port {PORT}"
    )

    app.run(

        host="0.0.0.0",

        port=PORT,

        debug=False,

        use_reloader=False

    )


# =========================================================
# PORT
# =========================================================

try:

    PORT = int(
        os.environ.get(
            "PORT",
            "10000"
        )
    )

except ValueError:

    PORT = 10000


# =========================================================
# التشغيل
# =========================================================

if __name__ == "__main__":

    # تشغيل Flask في Thread منفصل

    server_thread = threading.Thread(

        target=run_web_server,

        daemon=True

    )

    server_thread.start()

    # تشغيل بوت Telegram

    main()
