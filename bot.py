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

BOT_TOKEN = "8959881524:AAHJKmUz59xbPicuodo-W6prLRg-lJDnbyc"
ADMIN_ID = 8299101176

TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}/"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
KEYS_FILE = os.path.join(BASE_DIR, "keys.json")

user_states = {}

log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)

def load_keys():
    try:
        if not os.path.exists(KEYS_FILE):
            save_keys({})
        with open(KEYS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except:
        return {}

def save_keys(keys):
    try:
        with open(KEYS_FILE, "w", encoding="utf-8") as f:
            json.dump(keys, f, ensure_ascii=False, indent=2)
    except:
        pass

def is_expired(info):
    try:
        expire = datetime.strptime(info["expire"], "%Y-%m-%d %H:%M:%S")
        return datetime.now() >= expire
    except:
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

app = Flask(__name__)

@app.route('/check', methods=['GET', 'POST'])
def check_key():
    try:
        update_keys_status()
        
        # جمع كل البيانات الممكن إرسالها من اللعبة (JSON أو Form أو رابط)
        data = {}
        if request.is_json:
            data = request.get_json() or {}
        if not data:
            data = request.form.to_dict() or {}
        if not data:
            data = request.args.to_dict() or {}

        # التقاط المفتاح بأي مسمى محتمل
        key = str(data.get('key', '') or data.get('code', '') or data.get('serial', '')).strip()
        
        # التقاط الـ HWID أو الجهاز بأي مسمى محتمل يرسله المود ميو
        hwid = str(
            data.get('hwid', '') or 
            data.get('device', '') or 
            data.get('device_id', '') or 
            data.get('uuid', '') or 
            data.get('id', '') or 
            data.get('android_id', '')
        ).strip()

        # طباعة ما تستلمه اللعبة تماماً في السجلات لنراه بوضوح
        print(f"Received Request -> Key: '{key}' | HWID: '{hwid}' | Raw Data: {data}")

        if not key or not hwid:
            return jsonify({"success": False, "message": f"Key or HWID missing. Received Data: {data}"})

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

def send_msg(chat_id, text, kb=None):
    data = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
    if kb:
        data["reply_markup"] = {"keyboard": kb, "resize_keyboard": True}
    try:
        requests.post(TELEGRAM_API + "sendMessage", json=data, timeout=15)
    except:
        pass

def main_kb():
    return [
        [{"text": "🔑 إنشاء مفتاح"}, {"text": "📋 قائمة المفاتيح"}],
        [{"text": "🟢 تفعيل مفتاح"}, {"text": "🔴 إيقاف مفتاح"}],
        [{"text": "🔄 تصفير جهاز مفتاح"}, {"text": "🗑️ حذف مفتاح"}],
        [{"text": "🔎 معلومات مفتاح"}]
    ]

def bot_loop():
    offset = 0
    print("Bot Started...")
    while True:
        try:
            update_keys_status()
            res = requests.get(TELEGRAM_API + "getUpdates", params={"offset": offset, "timeout": 25}, timeout=35).json()
            if not res.get("ok"):
                time.sleep(2)
                continue

            for upd in res.get("result", []):
                offset = upd["update_id"] + 1
                msg = upd.get("message")
                if not msg: continue

                chat_id = msg["chat"]["id"]
                user_id = msg["from"]["id"]
                text = msg.get("text", "").strip()

                if user_id != ADMIN_ID:
                    send_msg(chat_id, "⛔ ليس لديك صلاحية.")
                    continue

                if text == "/start" or text == "🔙 رجوع":
                    user_states.pop(chat_id, None)
                    send_msg(chat_id, "🔐 *لوحة تحكم مفاتيح Moldes الرئيسية:*", main_kb())
                    continue

                state = user_states.get(chat_id)

                if text == "🔑 إنشاء مفتاح":
                    user_states[chat_id] = "gen_days"
                    send_msg(chat_id, "📅 أرسل عدد الأيام للمفتاح (مثلاً: 1 أو 7 أو 30):")
                    continue

                elif state == "gen_days":
                    try:
                        days = int(text)
                        if days <= 0: raise ValueError
                        keys = load_keys()
                        chars = string.ascii_uppercase + string.digits
                        key = "MOLDES-" + "-".join("".join(random.choices(chars, k=4)) for _ in range(3))
                        while key in keys:
                            key = "MOLDES-" + "-".join("".join(random.choices(chars, k=4)) for _ in range(3))

                        expire = (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
                        keys[key] = {
                            "active": True, "expired": False, "hwid": "",
                            "created": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                            "expire": expire
                        }
                        save_keys(keys)
                        user_states.pop(chat_id, None)
                        send_msg(chat_id, f"✅ *تم إنشاء المفتاح بنجاح*\n\n`{key}`\n\n🕐 الانتهاء:\n{expire}", main_kb())
                    except:
                        send_msg(chat_id, "❌ أرسل رقم صحيح للأيام:")
                    continue

                elif text == "🟢 تفعيل مفتاح":
                    user_states[chat_id] = "act_key"
                    send_msg(chat_id, "🟢 أرسل المفتاح لتفعيله:")
                    continue
                elif state == "act_key":
                    keys = load_keys()
                    user_states.pop(chat_id, None)
                    if text in keys:
                        keys[text]["active"] = True
                        keys[text]["expired"] = False
                        save_keys(keys)
                        send_msg(chat_id, "✅ تم التفعيل.", main_kb())
                    else:
                        send_msg(chat_id, "❌ غير موجود.", main_kb())
                    continue

                elif text == "🔴 إيقاف مفتاح":
                    user_states[chat_id] = "deact_key"
                    send_msg(chat_id, "🔴 أرسل المفتاح لإيقافه:")
                    continue
                elif state == "deact_key":
                    keys = load_keys()
                    user_states.pop(chat_id, None)
                    if text in keys:
                        keys[text]["active"] = False
                        save_keys(keys)
                        send_msg(chat_id, "✅ تم الإيقاف.", main_kb())
                    else:
                        send_msg(chat_id, "❌ غير موجود.", main_kb())
                    continue

                elif text == "🔄 تصفير جهاز مفتاح":
                    user_states[chat_id] = "reset_key"
                    send_msg(chat_id, "🔄 أرسل المفتاح لتصفير جهازه:")
                    continue
                elif state == "reset_key":
                    keys = load_keys()
                    user_states.pop(chat_id, None)
                    if text in keys:
                        keys[text]["hwid"] = ""
                        save_keys(keys)
                        send_msg(chat_id, "✅ تم التصفير بنجاح!", main_kb())
                    else:
                        send_msg(chat_id, "❌ غير موجود.", main_kb())
                    continue

                elif text == "🗑️ حذف مفتاح":
                    user_states[chat_id] = "del_key"
                    send_msg(chat_id, "🗑️ أرسل المفتاح لحذفه:")
                    continue
                elif state == "del_key":
                    keys = load_keys()
                    user_states.pop(chat_id, None)
                    if text in keys:
                        del keys[text]
                        save_keys(keys)
                        send_msg(chat_id, "✅ تم الحذف.", main_kb())
                    else:
                        send_msg(chat_id, "❌ غير موجود.", main_kb())
                    continue

                elif text == "🔎 معلومات مفتاح":
                    user_states[chat_id] = "info_key"
                    send_msg(chat_id, "🔎 أرسل المفتاح لمعرفة تفاصيله:")
                    continue
                elif state == "info_key":
                    keys = load_keys()
                    user_states.pop(chat_id, None)
                    if text in keys:
                        info = keys[text]
                        status = "🟢 مفعل" if info.get("active") else "🔴 متوقف"
                        dev = f"`{info.get('hwid')}`" if info.get("hwid") else "🟢 فارغ"
                        send_msg(chat_id, f"🔎 *المفتاح:* `{text}`\nالحالة: {status}\nالجهاز: {dev}\nالانتهاء: {info.get('expire')}", main_kb())
                    else:
                        send_msg(chat_id, "❌ غير موجود.", main_kb())
                    continue

                elif text == "📋 قائمة المفاتيح":
                    user_states.pop(chat_id, None)
                    keys = load_keys()
                    if not keys:
                        send_msg(chat_id, "📋 لا توجد مفاتيح.", main_kb())
                    else:
                        lines = ["📋 *المفاتيح:*"]
                        for k, info in keys.items():
                            st = "🟢" if info.get("active") else "🔴"
                            dev = "🔗" if info.get("hwid") else "🟢"
                            lines.append(f"{st} `{k}` | {dev}\n")
                        send_msg(chat_id, "\n".join(lines), main_kb())
                    continue

                send_msg(chat_id, "❓ استخدم الأزرار بالأسفل.", main_kb())
        except Exception as e:
            time.sleep(3)

if __name__ == "__main__":
    t = threading.Thread(target=bot_loop)
    t.daemon = True
    t.start()

    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
