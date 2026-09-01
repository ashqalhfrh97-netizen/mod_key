import requests
import json
import os
import random
import string
import time
from datetime import datetime, timedelta

# ==========================================
# إعدادات البوت
# ==========================================

BOT_TOKEN = "8959881524:AAHJKmUz59xbPicuodo-W6prLRg-lJDnbyc"
ADMIN_ID = 8299101176

TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}/"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
KEYS_FILE = os.path.join(BASE_DIR, "keys.json")

states = {}
custom_data = {}


# ==========================================
# قاعدة البيانات
# ==========================================

def load_keys():
    try:
        if not os.path.exists(KEYS_FILE):
            save_keys({})

        with open(KEYS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)

        return data if isinstance(data, dict) else {}

    except Exception as e:
        print("LOAD ERROR:", repr(e))
        return {}


def save_keys(keys):
    try:
        with open(KEYS_FILE, "w", encoding="utf-8") as f:
            json.dump(
                keys,
                f,
                ensure_ascii=False,
                indent=2
            )

    except Exception as e:
        print("SAVE ERROR:", repr(e))


# ==========================================
# Telegram
# ==========================================

def send_message(chat_id, text, keyboard=None):

    data = {
        "chat_id": chat_id,
        "text": text
    }

    if keyboard is not None:
        data["reply_markup"] = {
            "keyboard": keyboard,
            "resize_keyboard": True
        }

    try:
        r = requests.post(
            TELEGRAM_API + "sendMessage",
            json=data,
            timeout=20
        )

        print("SEND:", r.text)

    except Exception as e:
        print("TELEGRAM SEND ERROR:", repr(e))


# ==========================================
# إنشاء مفتاح
# ==========================================

def create_key():

    chars = string.ascii_uppercase + string.digits

    return (
        "MOLDES-"
        + "".join(random.choices(chars, k=4))
        + "-"
        + "".join(random.choices(chars, k=4))
        + "-"
        + "".join(random.choices(chars, k=4))
    )


# ==========================================
# إنشاء تاريخ الانتهاء
# ==========================================

def make_expire(days, hours, minutes):

    expire_time = (
        datetime.now()
        + timedelta(
            days=days,
            hours=hours,
            minutes=minutes
        )
    )

    return expire_time.strftime(
        "%Y-%m-%d %H:%M:%S"
    )


# ==========================================
# فحص انتهاء المفاتيح
# ==========================================

def is_expired(info):

    try:
        expire = datetime.strptime(
            info["expire"],
            "%Y-%m-%d %H:%M:%S"
        )

        return datetime.now() >= expire

    except Exception:
        return True


def update_expired_keys():

    keys = load_keys()
    changed = False

    for key, info in keys.items():

        if info.get("active", False):

            if is_expired(info):

                info["active"] = False
                info["expired"] = True

                changed = True

    if changed:
        save_keys(keys)


# ==========================================
# لوحة التحكم
# ==========================================

def main_keyboard():

    return [
        [
            {"text": "🔑 إنشاء مفتاح"},
            {"text": "📋 قائمة المفاتيح"}
        ],
        [
            {"text": "🟢 تفعيل مفتاح"},
            {"text": "🔴 إيقاف مفتاح"}
        ],
        [
            {"text": "🗑️ حذف مفتاح"},
            {"text": "🔎 معلومات مفتاح"}
        ]
    ]


def duration_keyboard():

    return [
        [
            {"text": "⚡ 1 يوم"},
            {"text": "📅 7 أيام"}
        ],
        [
            {"text": "📅 30 يوم"},
            {"text": "🛠️ مدة مخصصة"}
        ],
        [
            {"text": "🔙 رجوع"}
        ]
    ]


# ==========================================
# إنشاء المفتاح
# ==========================================

def generate_key(chat_id, days, hours, minutes):

    keys = load_keys()

    key = create_key()

    while key in keys:
        key = create_key()

    expire = make_expire(
        days,
        hours,
        minutes
    )

    keys[key] = {
        "active": True,
        "expired": False,
        "days": days,
        "hours": hours,
        "minutes": minutes,
        "created": datetime.now().strftime(
            "%Y-%m-%d %H:%M:%S"
        ),
        "expire": expire
    }

    save_keys(keys)

    send_message(
        chat_id,

        "✅ تم إنشاء المفتاح\n\n"
        f"🔑 {key}\n\n"
        f"📅 الأيام: {days}\n"
        f"⏰ الساعات: {hours}\n"
        f"⏱️ الدقائق: {minutes}\n\n"
        f"🕐 الانتهاء:\n{expire}",

        main_keyboard()
    )


# ==========================================
# تشغيل البوت
# ==========================================

def main():

    offset = 0

    print("Moldes Key Bot Started")

    while True:

        try:

            update_expired_keys()

            response = requests.get(
                TELEGRAM_API + "getUpdates",
                params={
                    "offset": offset,
                    "timeout": 30
                },
                timeout=40
            )

            data = response.json()

            if not data.get("ok", False):

                print("Telegram error:", data)

                time.sleep(3)
                continue

            updates = data.get(
                "result",
                []
            )

            for update in updates:

                offset = update["update_id"] + 1

                message = update.get("message")

                if not message:
                    continue

                chat_id = message["chat"]["id"]

                user_id = message["from"]["id"]

                text = message.get(
                    "text",
                    ""
                ).strip()

                print(
                    "Received:",
                    repr(text)
                )

                # ==================================
                # حماية الأدمن
                # ==================================

                if user_id != ADMIN_ID:

                    send_message(
                        chat_id,
                        "⛔ ليس لديك صلاحية استخدام هذا البوت."
                    )

                    continue

                # ==================================
                # START
                # ==================================

                if text == "/start":

                    states.pop(
                        chat_id,
                        None
                    )

                    custom_data.pop(
                        chat_id,
                        None
                    )

                    send_message(
                        chat_id,

                        "🔐 Moldes Key Bot\n\n"
                        "اختر العملية:",

                        main_keyboard()
                    )

                    continue

                # ==================================
                # رجوع
                # ==================================

                if text == "🔙 رجوع":

                    states.pop(
                        chat_id,
                        None
                    )

                    custom_data.pop(
                        chat_id,
                        None
                    )

                    send_message(
                        chat_id,
                        "🔐 القائمة الرئيسية:",
                        main_keyboard()
                    )

                    continue

                # ==================================
                # إنشاء مفتاح
                # ==================================

                if text == "🔑 إنشاء مفتاح":

                    states[chat_id] = "duration"

                    send_message(
                        chat_id,
                        "⏳ اختر مدة المفتاح:",
                        duration_keyboard()
                    )

                    continue

                # ==================================
                # مدد جاهزة
                # ==================================

                if states.get(chat_id) == "duration":

                    if text == "⚡ 1 يوم":

                        states.pop(
                            chat_id,
                            None
                        )

                        generate_key(
                            chat_id,
                            1,
                            0,
                            0
                        )

                        continue

                    if text == "📅 7 أيام":

                        states.pop(
                            chat_id,
                            None
                        )

                        generate_key(
                            chat_id,
                            7,
                            0,
                            0
                        )

                        continue

                    if text == "📅 30 يوم":

                        states.pop(
                            chat_id,
                            None
                        )

                        generate_key(
                            chat_id,
                            30,
                            0,
                            0
                        )

                        continue

                    if text == "🛠️ مدة مخصصة":

                        states[chat_id] = "days"

                        custom_data[chat_id] = {}

                        send_message(
                            chat_id,
                            "📅 اكتب عدد الأيام:"
                        )

                        continue

                # ==================================
                # الأيام
                # ==================================

                if states.get(chat_id) == "days":

                    try:

                        days = int(text)

                        if days < 0:
                            raise ValueError

                        custom_data[chat_id]["days"] = days

                        states[chat_id] = "hours"

                        send_message(
                            chat_id,
                            "⏰ اكتب عدد الساعات:\n"
                            "من 0 إلى 23"
                        )

                    except:

                        send_message(
                            chat_id,
                            "❌ اكتب رقمًا صحيحًا."
                        )

                    continue

                # ==================================
                # الساعات
                # ==================================

                if states.get(chat_id) == "hours":

                    try:

                        hours = int(text)

                        if hours < 0 or hours > 23:
                            raise ValueError

                        custom_data[chat_id]["hours"] = hours

                        states[chat_id] = "minutes"

                        send_message(
                            chat_id,
                            "⏱️ اكتب عدد الدقائق:\n"
                            "من 0 إلى 59"
                        )

                    except:

                        send_message(
                            chat_id,
                            "❌ الساعات يجب أن تكون من 0 إلى 23."
                        )

                    continue

                # ==================================
                # الدقائق
                # ==================================

                if states.get(chat_id) == "minutes":

                    try:

                        minutes = int(text)

                        if minutes < 0 or minutes > 59:
                            raise ValueError

                        days = custom_data[chat_id]["days"]
                        hours = custom_data[chat_id]["hours"]

                        if (
                            days == 0
                            and hours == 0
                            and minutes == 0
                        ):

                            send_message(
                                chat_id,
                                "❌ المدة يجب أن تكون أكبر من صفر."
                            )

                            continue

                        states.pop(
                            chat_id,
                            None
                        )

                        custom_data.pop(
                            chat_id,
                            None
                        )

                        generate_key(
                            chat_id,
                            days,
                            hours,
                            minutes
                        )

                    except:

                        send_message(
                            chat_id,
                            "❌ الدقائق يجب أن تكون من 0 إلى 59."
                        )

                    continue

                # ==================================
                # تفعيل مفتاح
                # ==================================

                if text == "🟢 تفعيل مفتاح":

                    states[chat_id] = "activate"

                    send_message(
                        chat_id,
                        "🟢 أرسل المفتاح الذي تريد تفعيله."
                    )

                    continue

                if states.get(chat_id) == "activate":

                    keys = load_keys()

                    if text not in keys:

                        send_message(
                            chat_id,
                            "❌ المفتاح غير موجود.",
                            main_keyboard()
                        )

                    else:

                        keys[text]["active"] = True
                        keys[text]["expired"] = False

                        save_keys(keys)

                        send_message(
                            chat_id,
                            "✅ تم تفعيل المفتاح.",
                            main_keyboard()
                        )

                    states.pop(
                        chat_id,
                        None
                    )

                    continue

                # ==================================
                # إيقاف مفتاح
                # ==================================

                if text == "🔴 إيقاف مفتاح":

                    states[chat_id] = "deactivate"

                    send_message(
                        chat_id,
                        "🔴 أرسل المفتاح الذي تريد إيقافه."
                    )

                    continue

                if states.get(chat_id) == "deactivate":

                    keys = load_keys()

                    if text not in keys:

                        send_message(
                            chat_id,
                            "❌ المفتاح غير موجود.",
                            main_keyboard()
                        )

                    else:

                        keys[text]["active"] = False

                        save_keys(keys)

                        send_message(
                            chat_id,
                            "✅ تم إيقاف المفتاح.",
                            main_keyboard()
                        )

                    states.pop(
                        chat_id,
                        None
                    )

                    continue

                # ==================================
                # حذف مفتاح
                # ==================================

                if text == "🗑️ حذف مفتاح":

                    states[chat_id] = "delete"

                    send_message(
                        chat_id,
                        "🗑️ أرسل المفتاح الذي تريد حذفه."
                    )

                    continue

                if states.get(chat_id) == "delete":

                    keys = load_keys()

                    if text not in keys:

                        send_message(
                            chat_id,
                            "❌ المفتاح غير موجود.",
                            main_keyboard()
                        )

                    else:

                        del keys[text]

                        save_keys(keys)

                        send_message(
                            chat_id,
                            "✅ تم حذف المفتاح.",
                            main_keyboard()
                        )

                    states.pop(
                        chat_id,
                        None
                    )

                    continue

                # ==================================
                # معلومات مفتاح
                # ==================================

                if text == "🔎 معلومات مفتاح":

                    states[chat_id] = "info"

                    send_message(
                        chat_id,
                        "🔎 أرسل المفتاح."
                    )

                    continue

                if states.get(chat_id) == "info":

                    keys = load_keys()

                    if text not in keys:

                        send_message(
                            chat_id,
                            "❌ المفتاح غير موجود.",
                            main_keyboard()
                        )

                    else:

                        info = keys[text]

                        expired = is_expired(info)

                        if expired:

                            info["active"] = False
                            info["expired"] = True

                            save_keys(keys)

                        status = (
                            "🟢 مفعل"
                            if info.get("active", False)
                            else "🔴 متوقف"
                        )

                        if info.get("expired", False):
                            status = "⌛ منتهي"

                        send_message(
                            chat_id,

                            "🔎 معلومات المفتاح\n\n"
                            f"🔑 {text}\n\n"
                            f"الحالة: {status}\n\n"
                            f"🕐 الانتهاء:\n"
                            f"{info.get('expire', '-')}",

                            main_keyboard()
                        )

                    states.pop(
                        chat_id,
                        None
                    )

                    continue

                # ==================================
                # قائمة المفاتيح
                # ==================================

                if text == "📋 قائمة المفاتيح":

                    keys = load_keys()

                    if not keys:

                        send_message(
                            chat_id,
                            "📋 لا توجد مفاتيح حاليًا.",
                            main_keyboard()
                        )

                    else:

                        lines = [
                            "📋 قائمة المفاتيح\n"
                        ]

                        for key, info in keys.items():

                            if is_expired(info):

                                info["active"] = False
                                info["expired"] = True

                            status = (
                                "🟢"
                                if info.get("active", False)
                                else "🔴"
                            )

                            lines.append(
                                f"{status} {key}\n"
                                f"⏰ {info.get('expire', '-')}\n"
                            )

                        save_keys(keys)

                        send_message(
                            chat_id,
                            "\n".join(lines),
                            main_keyboard()
                        )

                    continue

                # ==================================
                # غير معروف
                # ==================================

                send_message(
                    chat_id,
                    "❓ اختر أحد الأزرار.",
                    main_keyboard()
                )

        except Exception as e:

            print(
                "MAIN ERROR:",
                repr(e)
            )

            time.sleep(3)


if __name__ == "__main__":
    main()