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
            json.dump(keys, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print("SAVE ERROR:", repr(e))


# ==========================================
# فحص انتهاء المفاتيح
# ==========================================

def is_expired(info):
    try:
        expire = datetime.strptime(info["expire"], "%Y-%m-%d %H:%M:%S")
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

@app.route('/check', methods=['GET', 'POST'])
def api_check_key():
    try:
        update_expired_keys()
        
        # استخراج البيانات بدقة من أي نوع طلب ترسله اللعبة (POST/GET/JSON)
        key = ""
        hwid = ""
        
        if request.method == 'POST':
            if request.is_json:
                json_data = request.get_json() or {}
                key = str(json_data.get('key', '')).strip()
                hwid = str(json_data.get('hwid', '') or json_data.get('device', '') or json_data.get('device_id', '')).strip()
            else:
                key = str(request.form.get('key', '')).strip()
                hwid = str(request.form.get('hwid', '') or request.form.get('device', '') or request.form.get('device_id', '')).strip()
        
        if not key or not hwid:
            # محاولة أخيرة من الـ Args لو كانت مرسلة عبر الرابط مباشرة
            key = key or str(request.args.get('key', '')).strip()
            hwid = hwid or str(request.args.get('hwid', '') or request.args.get('device', '')).strip()

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

        # التحقق من الجهاز (جهاز واحد فقط)
        saved_hwid = info.get("hwid", "")

        if saved_hwid == hwid:
            return jsonify({"success": True, "message": "Key is valid"})

        if not saved_hwid:
            info["hwid"] = hwid
            save_keys(keys)
            return jsonify({"success": True, "message": "Key is valid"})
        else:
            return jsonify({"success": False, "message": "Key already used on another device"})

    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


# ==========================================
# Telegram Functions & Keyboards
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
        requests.post(TELEGRAM_API + "sendMessage", json=data, timeout=20)
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
    expire_time = datetime.now() + timedelta(days=days, hours=hours, minutes=minutes)
    return expire_time.strftime("%Y-%m-%d %H:%M:%S")


def main_keyboard():
    return [
        [{"text": "🔑 إنشاء مفتاح"}, {"text": "📋 قائمة المفاتيح"}],
        [{"text": "🟢 تفعيل مفتاح"}, {"text": "🔴 إيقاف مفتاح"}],
        [{"text": "🔄 تصفير جهاز مفتاح"}, {"text": "🗑️ حذف مفتاح"}],
        [{"text": "🔎 معلومات مفتاح"}]
    ]


def duration_keyboard():
    return [
        [{"text": "⚡ 1 يوم"}, {"text": "📅 7 أيام"}],
        [{"text": "📅 30 يوم"}, {"text": "🛠️ مدة مخصصة"}],
        [{"text": "🔙 رجوع"}]
    ]


def generate_key(chat_id, days, hours, minutes):
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
        "hwid": "",
        "created": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "expire": expire
    }

    save_keys(keys)

    send_message(
        chat_id,
        "✅ *تم إنشاء المفتاح بنجاح (لجهاز واحد)*\n\n"
        f"`{key}`\n\n"
        f"📅 الأيام: {days} | ⏰ الساعات: {hours}\n"
        f"🕐 الانتهاء:\n{expire}",
        main_keyboard()
    )


# ==========================================
# تشغيل البوت الأساسي
# ==========================================

def main():
    offset = 0
    print("Moldes Key Bot Started Clean Mode")

    while True:
        try:
            update_expired_keys()

            response = requests.get(
                TELEGRAM_API + "getUpdates",
                params={"offset": offset, "timeout": 30},
                timeout=40
            )

            data = response.json()
            if not data.get("ok", False):
                time.sleep(3)
                continue

            for update in data.get("result", []):
                offset = update["update_id"] + 1
                message = update.get("message")
                if not message:
                    continue

                chat_id = message["chat"]["id"]
                user_id = message["from"]["id"]
                text = message.get("text", "").strip()

                if user_id != ADMIN_ID:
                    send_message(chat_id, "⛔ ليس لديك صلاحية استخدام هذا البوت.")
                    continue

                if text == "/start":
                    states.pop(chat_id, None)
                    custom_data.pop(chat_id, None)
                    send_message(chat_id, "🔐 *لوحة تحكم مفاتيح Moldes*\n\nاختر العملية:", main_keyboard())
                    continue

                if text == "🔙 رجوع":
                    states.pop(chat_id, None)
                    custom_data.pop(chat_id, None)
                    send_message(chat_id, "🔐 *القائمة الرئيسية:*", main_keyboard())
                    continue

                current_state = states.get(chat_id)

                # الأزرار الرئيسية
                if text == "🔑 إنشاء مفتاح":
                    states[chat_id] = "duration"
                    send_message(chat_id, "⏳ *اختر مدة المفتاح:*", duration_keyboard())
                    continue

                elif text == "🟢 تفعيل مفتاح":
                    states[chat_id] = "activate"
                    send_message(chat_id, "🟢 أرسل المفتاح الذي تريد تفعيله:")
                    continue

                elif text == "🔴 إيقاف مفتاح":
                    states[chat_id] = "deactivate"
                    send_message(chat_id, "🔴 أرسل المفتاح الذي تريد إيقافه:")
                    continue

                elif text == "🔄 تصفير جهاز مفتاح":
                    states[chat_id] = "reset_hwid"
                    send_message(chat_id, "🔄 أرسل المفتاح لتفريغ الجهاز المرتبط به:")
                    continue

                elif text == "🗑️ حذف مفتاح":
                    states[chat_id] = "delete"
                    send_message(chat_id, "🗑️ أرسل المفتاح الذي تريد حذفه نهائياً:")
                    continue

                elif text == "🔎 معلومات مفتاح":
                    states[chat_id] = "info"
                    send_message(chat_id, "🔎 أرسل المفتاح لعرض تفاصيله:")
                    continue

                elif text == "📋 قائمة المفاتيح":
                    states.pop(chat_id, None)
                    keys = load_keys()
                    if not keys:
                        send_message(chat_id, "📋 لا توجد مفاتيح حاليًا.", main_keyboard())
                    else:
                        lines = ["📋 *قائمة المفاتيح الكاملة:*\n"]
                        for key, info in keys.items():
                            if is_expired(info):
                                info["active"] = False
                                info["expired"] = True
                            status = "🟢" if info.get("active", False) else "🔴"
                            bound = "🔗 مرتبط" if info.get("hwid") else "🟢 فارغ"
                            lines.append(f"{status} `{key}` | {bound}\n⏰ {info.get('expire', '-')}\n")
                        save_keys(keys)
                        send_message(chat_id, "\n".join(lines), main_keyboard())
                    continue

                # معالجة الحالات (States)
                if current_state == "duration":
                    if text == "⚡ 1 يوم":
                        states.pop(chat_id, None)
                        custom_data.pop(chat_id, None)
                        generate_key(chat_id, 1, 0, 0)
                        continue
                    elif text == "📅 7 أيام":
                        states.pop(chat_id, None)
                        custom_data.pop(chat_id, None)
                        generate_key(chat_id, 7, 0, 0)
                        continue
                    elif text == "📅 30 يوم":
                        states.pop(chat_id, None)
                        custom_data.pop(chat_id, None)
                        generate_key(chat_id, 30, 0, 0)
                        continue
                    elif text == "🛠️ مدة مخصصة":
                        states[chat_id] = "days"
                        custom_data[chat_id] = {}
                        send_message(chat_id, "📅 اكتب عدد الأيام:")
                        continue
                    else:
                        send_message(chat_id, "❌ يرجى اختيار المدة من الأزرار المتاحة أدناه.")
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
                        days = custom_data[chat_id]["days"]
                        hours = custom_data[chat_id]["hours"]
                        if days == 0 and hours == 0 and minutes == 0:
                            send_message(chat_id, "❌ المدة يجب أن تكون أكبر من صفر.")
                            states[chat_id] = "days"
                            continue
                        
                        states.pop(chat_id, None)
                        custom_data.pop(chat_id, None)
                        generate_key(chat_id, days, hours, minutes)
                    except:
                        send_message(chat_id, "❌ الدقائق يجب أن تكون من 0 إلى 59.")
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

                elif current_state == "reset_hwid":
                    keys = load_keys()
                    states.pop(chat_id, None)
                    if text not in keys:
                        send_message(chat_id, "❌ المفتاح غير موجود.", main_keyboard())
                    else:
                        keys[text]["hwid"] = ""
                        save_keys(keys)
                        send_message(chat_id, "✅ تم تفريغ الجهاز المرتبط بالمفتاح بنجاح!", main_keyboard())
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

                        bound = "مفعل على جهاز" if info.get("hwid") else "غير مرتبط بجهاز"

                        send_message(
                            chat_id,
                            "🔎 *معلومات المفتاح*\n\n"
                            f"`{text}`\n\n"
                            f"الحالة: {status}\n"
                            f"📱 الجهاز: {bound}\n\n"
                            f"🕐 الانتهاء:\n{info.get('expire', '-')}",
                            main_keyboard()
                        )
                    continue

                send_message(chat_id, "❓ يرجى اختيار أحد الأزرار الظاهرة في القائمة.", main_keyboard())

        except Exception as e:
            print("MAIN ERROR:", repr(e))
            time.sleep(3)


if __name__ == "__main__":
    telegram_thread = threading.Thread(target=main)
    telegram_thread.daemon = True
    telegram_thread.start()

    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
