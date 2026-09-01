import requests
import json
import os
import random
import string
import time
from datetime import datetime, timedelta
import threading
from flask import Flask, request, jsonify

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
# إعداد سيرفر الويب (Flask API)
# ==========================================

app = Flask(__name__)

@app.route('/check', methods=['POST'])
def api_check_key():
    try:
        update_expired_keys()
        
        key = request.form.get('key', '').strip() or request.args.get('key', '').strip()
        hwid = request.form.get('hwid', '').strip() or request.args.get('hwid', '').strip()

        if not key or not hwid:
            return jsonify({"success": False, "message": "Key or HWID missing"})

        keys = load_keys()

        if key not in keys:
            return jsonify({"success": False, "message": "Key not found"})

        info = keys[key]

        if is_expired(info):
            info["active"] = False
            info["expired"] = True
            save_keys(keys)
            return jsonify({"success": False, "message": "Key expired"})

        if not info.get("active", False):
            return jsonify({"success": False, "message": "Key is inactive"})

        max_devices = info.get("max_devices", 1)
        devices = info.get("devices", [])

        if hwid in devices:
            return jsonify({"success": True, "message": "Key is valid"})

        if len(devices) < max_devices:
            devices.append(hwid)
            info["devices"] = devices
            save_keys(keys)
            return jsonify({"success": True, "message": "Key is valid"})
        else:
            return jsonify({"success": False, "message": "Key used on max devices limit"})

    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


def run_web_server():
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)


# ==========================================
# Telegram Functions
# ==========================================

def send_message(chat_id, text, keyboard=None, parse_mode="Markdown"):
    data = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": parse_mode
    }

    if keyboard is not None:
        data["reply_markup"] = {
            "keyboard": keyboard,
            "resize_keyboard": True
        }

    try:
        requests.post(
            TELEGRAM_API + "sendMessage",
            json=data,
            timeout=20
        )
    except Exception as e:
        print("TELEGRAM SEND ERROR:", repr(e))


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
            {"text": "🔄 تصفير أجهزة مفتاح"},
            {"text": "🗑️ حذف مفتاح"}
        ],
        [
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


def devices_keyboard():
    return [
        [
            {"text": "📱 1 جهاز"},
            {"text": "📱📱 2 أجهزة"}
        ],
        [
            {"text": "📱📱📱 3 أجهزة"},
            {"text": "♾️ أجهزة غير محدودة"}
        ],
        [
            {"text": "🔙 رجوع"}
        ]
    ]


def generate_key(chat_id, days, hours, minutes, max_devices):
    keys = load_keys()
    key = create_key()

    while key in keys:
        key = create_key()

    expire = make_expire(days, hours, minutes)

    keys[key] = {
        "active": True,
        "expired": False,
        "days": days,
        "hours": hours,
        "minutes": minutes,
        "max_devices": max_devices,
        "devices": [],
        "created": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "expire": expire
    }

    save_keys(keys)

    dev_text = "غير محدودة" if max_devices == 999 else str(max_devices)

    send_message(
        chat_id,
        "✅ *تم إنشاء المفتاح بنجاح*\n*(اضغط على المفتاح أدناه للنسخ السريع)*\n\n"
        f"`{key}`\n\n"
        f"📅 الأيام: {days} | ⏰ الساعات: {hours}\n"
        f"📱 الحد الأقصى للأجهزة: {dev_text}\n"
        f"🕐 الانتهاء:\n{expire}",
        main_keyboard()
    )


# ==========================================
# تشغيل البوت الأساسي
# ==========================================

def main():
    offset = 0
    print("Moldes Key Bot Started Safely")

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
                time.sleep(3)
                continue

            updates = data.get("result", [])

            for update in updates:
                offset = update["update_id"] + 1
                message = update.get("message")

                if not message:
                    continue

                chat_id = message["chat"]["id"]
                user_id = message["from"]["id"]
                text = message.get("text", "").strip()

                if user_id != ADMIN_ID:
                    send_message(
                        chat_id,
                        "⛔ ليس لديك صلاحية استخدام هذا البوت."
                    )
                    continue

                if text == "/start":
                    states.pop(chat_id, None)
                    custom_data.pop(chat_id, None)
                    send_message(
                        chat_id,
                        "🔐 *لوحة تحكم مفاتيح Moldes*\n\nاختر العملية:",
                        main_keyboard()
                    )
                    continue

                if text == "🔙 رجوع":
                    states.pop(chat_id, None)
                    custom_data.pop(chat_id, None)
                    send_message(
                        chat_id,
                        "🔐 *القائمة الرئيسية:*",
                        main_keyboard()
                    )
                    continue

                current_state = states.get(chat_id)

                # 1. إنشاء مفتاح - اختيار المدة
                if text == "🔑 إنشاء مفتاح":
                    states[chat_id] = "duration"
                    send_message(
                        chat_id,
                        "⏳ *اختر مدة المفتاح:*",
                        duration_keyboard()
                    )
                    continue

                if current_state == "duration":
                    if text == "⚡ 1 يوم":
                        custom_data[chat_id] = {"days": 1, "hours": 0, "minutes": 0}
                        states[chat_id] = "devices_count"
                        send_message(chat_id, "📱 *اختر عدد الأجهزة المسموح بها للكود:*", devices_keyboard())
                        continue
                    elif text == "📅 7 أيام":
                        custom_data[chat_id] = {"days": 7, "hours": 0, "minutes": 0}
                        states[chat_id] = "devices_count"
                        send_message(chat_id, "📱 *اختر عدد الأجهزة المسموح بها للكود:*", devices_keyboard())
                        continue
                    elif text == "📅 30 يوم":
                        custom_data[chat_id] = {"days": 30, "hours": 0, "minutes": 0}
                        states[chat_id] = "devices_count"
                        send_message(chat_id, "📱 *اختر عدد الأجهزة المسموح بها للكود:*", devices_keyboard())
                        continue
                    elif text == "🛠️ مدة مخصصة":
                        states[chat_id] = "days"
                        custom_data[chat_id] = {}
                        send_message(chat_id, "📅 اكتب عدد الأيام:")
                        continue

                elif current_state == "days":
                    try:
                        days = int(text)
                        if days < 0:
                            raise ValueError
                        custom_data[chat_id]["days"] = days
                        states[chat_id] = "hours"
                        send_message(chat_id, "⏰ اكتب عدد الساعات:\nمن 0 إلى 23")
                    except:
                        send_message(chat_id, "❌ اكتب رقمًا صحيحًا للأيام.")
                    continue

                elif current_state == "hours":
                    try:
                        hours = int(text)
                        if hours < 0 or hours > 23:
                            raise ValueError
                        custom_data[chat_id]["hours"] = hours
                        states[chat_id] = "minutes"
                        send_message(chat_id, "⏱️ اكتب عدد الدقائق:\nمن 0 إلى 59")
                    except:
                        send_message(chat_id, "❌ الساعات يجب أن تكون من 0 إلى 23.")
                    continue

                elif current_state == "minutes":
                    try:
                        minutes = int(text)
                        if minutes < 0 or minutes > 59:
                            raise ValueError
                        custom_data[chat_id]["minutes"] = minutes
                        days = custom_data[chat_id]["days"]
                        hours = custom_data[chat_id]["hours"]
                        if days == 0 and hours == 0 and minutes == 0:
                            send_message(chat_id, "❌ المدة يجب أن تكون أكبر من صفر.")
                            states[chat_id] = "days"
                            continue
                        states[chat_id] = "devices_count"
                        send_message(chat_id, "📱 *اختر عدد الأجهزة المسموح بها للكود:*", devices_keyboard())
                    except:
                        send_message(chat_id, "❌ الدقائق يجب أن تكون من 0 إلى 59.")
                    continue

                elif current_state == "devices_count":
                    max_dev = 1
                    if text == "📱 1 جهاز":
                        max_dev = 1
                    elif text == "📱📱 2 أجهزة":
                        max_dev = 2
                    elif text == "📱📱📱 3 أجهزة":
                        max_dev = 3
                    elif text == "♾️ أجهزة غير محدودة":
                        max_dev = 999
                    else:
                        send_message(chat_id, "❌ يرجى الاختيار من الأزرار الظاهرة أدناه.")
                        continue

                    d = custom_data[chat_id].get("days", 0)
                    h = custom_data[chat_id].get("hours", 0)
                    m = custom_data[chat_id].get("minutes", 0)
                    
                    states.pop(chat_id, None)
                    custom_data.pop(chat_id, None)
                    generate_key(chat_id, d, h, m, max_dev)
                    continue

                # 2. تفعيل مفتاح
                if text == "🟢 تفعيل مفتاح":
                    states[chat_id] = "activate"
                    send_message(chat_id, "🟢 أرسل المفتاح الذي تريد تفعيله:")
                    continue

                elif current_state == "activate":
                    keys = load_keys()
                    states.pop(chat_id, None)
                    if text not in keys:
                        send_message(chat_id, "❌ المفتاح غير موجود.", main_keyboard())
                    else:
                        keys[text]["active"] = True
                        keys[text]["expired"] = False
                        save_keys(keys)
                        send_message(chat_id, "✅ تم تفعيل المفتاح بنجاح.", main_keyboard())
                    continue

                # 3. إيقاف مفتاح
                if text == "🔴 إيقاف مفتاح":
                    states[chat_id] = "deactivate"
                    send_message(chat_id, "🔴 أرسل المفتاح الذي تريد إيقافه:")
                    continue

                elif current_state == "deactivate":
                    keys = load_keys()
                    states.pop(chat_id, None)
                    if text not in keys:
                        send_message(chat_id, "❌ المفتاح غير موجود.", main_keyboard())
                    else:
                        keys[text]["active"] = False
                        save_keys(keys)
                        send_message(chat_id, "✅ تم إيقاف المفتاح بنجاح.", main_keyboard())
                    continue

                # 4. تصفير الأجهزة
                if text == "🔄 تصفير أجهزة مفتاح":
                    states[chat_id] = "reset_hwid"
                    send_message(chat_id, "🔄 أرسل المفتاح لتفريغ أجهزته المسجلة:")
                    continue

                elif current_state == "reset_hwid":
                    keys = load_keys()
                    states.pop(chat_id, None)
                    if text not in keys:
                        send_message(chat_id, "❌ المفتاح غير موجود.", main_keyboard())
                    else:
                        keys[text]["devices"] = []
                        save_keys(keys)
                        send_message(chat_id, "✅ تم تصفير أجهزة المفتاح بنجاح!", main_keyboard())
                    continue

                # 5. حذف مفتاح
                if text == "🗑️ حذف مفتاح":
                    states[chat_id] = "delete"
                    send_message(chat_id, "🗑️ أرسل المفتاح الذي تريد حذفه نهائياً:")
                    continue

                elif current_state == "delete":
                    keys = load_keys()
                    states.pop(chat_id, None)
                    if text not in keys:
                        send_message(chat_id, "❌ المفتاح غير موجود.", main_keyboard())
                    else:
                        del keys[text]
                        save_keys(keys)
                        send_message(chat_id, "✅ تم حذف المفتاح نهائياً.", main_keyboard())
                    continue

                # 6. معلومات مفتاح
                if text == "🔎 معلومات مفتاح":
                    states[chat_id] = "info"
                    send_message(chat_id, "🔎 أرسل المفتاح لعرض تفاصيله:")
                    continue

                elif current_state == "info":
                    keys = load_keys()
                    states.pop(chat_id, None)
                    if text not in keys:
                        send_message(chat_id, "❌ المفتاح غير موجود.", main_keyboard())
                    else:
                        info = keys[text]
                        if is_expired(info):
                            info["active"] = False
                            info["expired"] = True
                            save_keys(keys)

                        status = "🟢 مفعل" if info.get("active", False) else "🔴 متوقف"
                        if info.get("expired", False):
                            status = "⌛ منتهي"

                        devices_list = info.get("devices", [])
                        max_dev = info.get("max_devices", 1)
                        max_str = "غير محدودة" if max_dev == 999 else str(max_dev)
                        dev_info = f"المسجلة ({len(devices_list)} من {max_str})"

                        send_message(
                            chat_id,
                            "🔎 *معلومات المفتاح*\n\n"
                            f"`{text}`\n\n"
                            f"الحالة: {status}\n"
                            f"📱 الأجهزة: {dev_info}\n\n"
                            f"🕐 الانتهاء:\n{info.get('expire', '-')}",
                            main_keyboard()
                        )
                    continue

                # 7. قائمة المفاتيح
                if text == "📋 قائمة المفاتيح":
                    states.pop(chat_id, None)
                    keys = load_keys()
                    if not keys:
                        send_message(chat_id, "📋 لا توجد مفاتيح حاليًا.", main_keyboard())
                    else:
                        lines = ["📋 *قائمة المفاتيح الكاملة:*\n*(اضغط على أي مفتاح لنسخه)*\n"]
                        for key, info in keys.items():
                            if is_expired(info):
                                info["active"] = False
                                info["expired"] = True
                            status = "🟢" if info.get("active", False) else "🔴"
                            dev_count = len(info.get("devices", []))
                            max_dev = info.get("max_devices", 1)
                            max_s = "∞" if max_dev == 999 else str(max_dev)
                            lines.append(f"{status} `{key}` | 📱 {dev_count}/{max_s}\n⏰ {info.get('expire', '-')}\n")
                        save_keys(keys)
                        send_message(chat_id, "\n".join(lines), main_keyboard())
                    continue

                # إذا لم تطابق أي حالة
                send_message(chat_id, "❓ يرجى اختيار أحد الأزرار الظاهرة في القائمة الرئيسية.", main_keyboard())

        except Exception as e:
            print("MAIN ERROR:", repr(e))
            time.sleep(3)


if __name__ == "__main__":
    telegram_thread = threading.Thread(target=main)
    telegram_thread.daemon = True
    telegram_thread.start()

    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
