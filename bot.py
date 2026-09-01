import requests
import json
import os
import random
import string
import time
from datetime import datetime, timedelta
import threading
import logging
from flask import Flask, request, jsonify

# ==========================================
# إعدادات البوت والسيرفر
# ==========================================

BOT_TOKEN = "8959881524:AAHJKmUz59xbPicuodo-W6prLRg-lJDnbyc"
ADMIN_ID = 8299101176

TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}/"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
KEYS_FILE = os.path.join(BASE_DIR, "keys.json")

user_states = {}
user_data = {}

# إيقاف سجلات الـ Flask المزعجة ليبقى السيرفر نظيفاً
log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)


# ==========================================
# إدارة قاعدة البيانات (المفاتيح)
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


def is_expired(info):
    try:
        expire = datetime.strptime(info["expire"], "%Y-%m-%d %H:%M:%S")
        return datetime.now() >= expire
    except Exception:
        return True


def update_keys_status():
    keys = load_keys()
    changed = False
    for k, info in keys.items():
        if info.get("active", False) and is_expired(info):
            info["active"] = False
            info["expired"] = True
            changed = True
    if changed:
        save_keys(keys)


# ==========================================
# إعداد سيرفر الويب (Flask API للتحقق)
# ==========================================

app = Flask(__name__)

@app.route('/check', methods=['GET', 'POST'])
def check_key():
    try:
        update_keys_status()
        
        # التقاط البيانات سواء أرسلت كـ JSON أو Form أو Args
        req = {}
        if request.is_json:
            req = request.get_json() or {}
        if not req:
            req = request.form.to_dict() or request.args.to_dict()

        key = str(req.get('key', '')).strip()
        hwid = str(req.get('hwid', '') or req.get('device', '') or req.get('device_id', '') or req.get('ID', '')).strip()

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
# وظائف وإشعارات تيليجرام
# ==========================================

def send_msg(chat_id, text, kb=None):
    data = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
    if kb:
        data["reply_markup"] = {"keyboard": kb, "resize_keyboard": True}
    try:
        requests.post(TELEGRAM_API + "sendMessage", json=data, timeout=15)
    except Exception as e:
        print("TELEGRAM SEND ERROR:", repr(e))


def main_kb():
    return [
        [{"text": "🔑 إنشاء مفتاح"}, {"text": "📋 قائمة المفاتيح"}],
        [{"text": "🟢 تفعيل مفتاح"}, {"text": "🔴 إيقاف مفتاح"}],
        [{"text": "🔄 تصفير جهاز مفتاح"}, {"text": "🗑️ حذف مفتاح"}],
        [{"text": "🔎 معلومات مفتاح"}]
    ]


# ==========================================
# تشغيل بوت تيليجرام (Loop)
# ==========================================

def bot_loop():
    offset = 0
    print("Moldes Bot Started Successfully...")

    while True:
        try:
            update_keys_status()

            res = requests.get(
                TELEGRAM_API + "getUpdates",
                params={"offset": offset, "timeout": 25},
                timeout=35
            ).json()

            if not res.get("ok"):
                time.sleep(2)
                continue

            for upd in res.get("result", []):
                offset = upd["update_id"] + 1
                msg = upd.get("message")
                if not msg:
                    continue

                chat_id = msg["chat"]["id"]
                user_id = msg["from"]["id"]
                text = msg.get("text", "").strip()

                if user_id != ADMIN_ID:
                    send_msg(chat_id, "⛔ ليس لديك صلاحية استخدام هذا البوت.")
                    continue

                if text == "/start" or text == "🔙 رجوع":
                    user_states.pop(chat_id, None)
                    user_data.pop(chat_id, None)
                    send_msg(chat_id, "🔐 *لوحة تحكم مفاتيح Moldes الرئيسية:*", main_kb())
                    continue

                state = user_states.get(chat_id)

                # العمليات الرئيسية
                if text == "🔑 إنشاء مفتاح":
                    user_states[chat_id] = "gen_days"
                    send_msg(chat_id, "📅 أرسل عدد الأيام للمفتاح (مثلاً: 1 أو 7 أو 30):")
                    continue

                elif text == "🟢 تفعيل مفتاح":
                    user_states[chat_id] = "act_key"
                    send_msg(chat_id, "🟢 أرسل المفتاح الذي تريد تفعيله:")
                    continue

                elif text == "🔴 إيقاف مفتاح":
                    user_states[chat_id] = "deact_key"
                    send_msg(chat_id, "🔴 أرسل المفتاح الذي تريد إيقافه:")
                    continue

                elif text == "🔄 تصفير جهاز مفتاح":
                    user_states[chat_id] = "reset_key"
                    send_msg(chat_id, "🔄 أرسل المفتاح لتفريغ الجهاز المرتبط به:")
                    continue

                elif text == "🗑️ حذف مفتاح":
                    user_states[chat_id] = "del_key"
                    send_msg(chat_id, "🗑️ أرسل المفتاح الذي تريد حذفه نهائياً:")
                    continue

                elif text == "🔎 معلومات مفتاح":
                    user_states[chat_id] = "info_key"
                    send_msg(chat_id, "🔎 أرسل المفتاح لمعرفة تفاصيله وجهازه المرتبط:")
                    continue

                elif text == "📋 قائمة المفاتيح":
                    user_states.pop(chat_id, None)
                    keys = load_keys()
                    if not keys:
                        send_msg(chat_id, "📋 لا توجد مفاتيح حالياً.", main_kb())
                    else:
                        lines = ["📋 *قائمة المفاتيح:*"]
                        for k, info in keys.items():
                            st = "🟢" if info.get("active") else "🔴"
                            dev = "🔗 مرتبط" if info.get("hwid") else "🟢 فارغ"
                            lines.append(f"{st} `{k}` | {dev}\n")
                        send_msg(chat_id, "\n".join(lines), main_kb())
                    continue

                # معالجة الخطوات (States)
                if state == "gen_days":
                    try:
                        days = int(text)
                        if days <= 0:
                            raise ValueError

                        keys = load_keys()
                        chars = string.ascii_uppercase + string.digits
                        key = "MOLDES-" + "-".join("".join(random.choices(chars, k=4)) for _ in range(3))
                        while key in keys:
                            key = "MOLDES-" + "-".join("".join(random.choices(chars, k=4)) for _ in range(3))

                        expire = (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
                        keys[key] = {
                            "active": True,
                            "expired": False,
                            "hwid": "",
                            "created": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                            "expire": expire
                        }
                        save_keys(keys)
                        user_states.pop(chat_id, None)
                        send_msg(
                            chat_id,
                            "✅ *تم إنشاء المفتاح بنجاح (لجهاز واحد)*\n\n"
                            f"`{key}`\n\n"
                            f"📅 المدة: {days} أيام\n"
                            f"🕐 الانتهاء:\n{expire}",
                            main_kb()
                        )
                    except Exception:
                        send_msg(chat_id, "❌ يرجى إرسال رقم صحيح للأيام (أكبر من صفر):")
                    continue

                elif state == "act_key":
                    keys = load_keys()
                    user_states.pop(chat_id, None)
                    if text in keys:
                        keys[text]["active"] = True
                        keys[text]["expired"] = False
                        save_keys(keys)
                        send_msg(chat_id, "✅ تم تفعيل المفتاح بنجاح.", main_kb())
                    else:
                        send_msg(chat_id, "❌ المفتاح غير موجود.", main_kb())
                    continue

                elif state == "deact_key":
                    keys = load_keys()
                    user_states.pop(chat_id, None)
                    if text in keys:
                        keys[text]["active"] = False
                        save_keys(keys)
                        send_msg(chat_id, "✅ تم إيقاف المفتاح بنجاح.", main_kb())
                    else:
                        send_msg(chat_id, "❌ المفتاح غير موجود.", main_kb())
                    continue

                elif state == "reset_key":
                    keys = load_keys()
                    user_states.pop(chat_id, None)
                    if text in keys:
                        keys[text]["hwid"] = ""
                        save_keys(keys)
                        send_msg(chat_id, "✅ تم تصفير الجهاز المرتبط بالمفتاح بنجاح!", main_kb())
                    else:
                        send_msg(chat_id, "❌ المفتاح غير موجود.", main_kb())
                    continue

                elif state == "del_key":
                    keys = load_keys()
                    user_states.pop(chat_id, None)
                    if text in keys:
                        del keys[text]
                        save_keys(keys)
                        send_msg(chat_id, "✅ تم حذف المفتاح نهائياً.", main_kb())
                    else:
                        send_msg(chat_id, "❌ المفتاح غير موجود.", main_kb())
                    continue

                elif state == "info_key":
                    keys = load_keys()
                    user_states.pop(chat_id, None)
                    if text in keys:
                        info = keys[text]
                        status = "🟢 مفعل" if info.get("active") else "🔴 متوقف"
                        bound = f"🔗 `{info.get('hwid')}`" if info.get("hwid") else "🟢 فارغ (متاح)"
                        send_msg(
                            chat_id,
                            "🔎 *معلومات المفتاح*\n\n"
                            f"`{text}`\n\n"
                            f"الحالة: {status}\n"
                            f"الجهاز المرتبط:\n{bound}\n\n"
                            f"🕐 الانتهاء:\n{info.get('expire')}",
                            main_kb()
                        )
                    else:
                        send_msg(chat_id, "❌ المفتاح غير موجود.", main_kb())
                    continue

                send_msg(chat_id, "❓ يرجى استخدام الأزرار الظاهرة في الأسفل.", main_kb())

        except Exception as e:
            print("LOOP ERROR:", repr(e))
            time.sleep(3)


# ==========================================
# تشغيل التطبيق (السيرفر والبوت معاً)
# ==========================================

if __name__ == "__main__":
    telegram_thread = threading.Thread(target=bot_loop)
    telegram_thread.daemon = True
    telegram_thread.start()

    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
