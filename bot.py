import os
import json
import random
import string
from flask import Flask, request, jsonify

app = Flask(__name__)

# ملف تخزين المفاتيح
KEYS_FILE = "keys_database.json"

def load_db():
    if not os.path.exists(KEYS_FILE):
        return {}
    try:
        with open(KEYS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {}

def save_db(data):
    with open(KEYS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

# 1. مسار توليد مفتاح جديد (يمكنك حمايته بكلمة سر خاصة بك أو استخدامه مباشرة)
@app.route("/generate", methods=["GET"])
def generate_key():
    # توليد مفتاح عشوائي قوي وغير قابل للتخمين
    chars = string.ascii_uppercase + string.digits
    key = "AK-" + "".join(random.choices(chars, k=4)) + "-" + "".join(random.choices(chars, k=4)) + "-" + "".join(random.choices(chars, k=4))
    
    db = load_db()
    db[key] = {
        "hwid": None,  # لم يتم ربطه بأي جهاز بعد
        "active": True
    }
    save_db(db)
    
    return jsonify({"success": True, "key": key})

# 2. مسار التحقق الأساسي للمود (Check API)
@app.route("/check", methods=["POST", "GET"])
def check_key():
    # استلام المفتاح وبصمة جهاز المستخدم (HWID) المرسلة من المود
    key = request.form.get("key") or request.args.get("key")
    hwid = request.form.get("hwid") or request.args.get("hwid")
    
    if not key:
        return jsonify({"success": False, "message": "No key provided"}), 400

    db = load_db()
    
    if key not in db:
        return jsonify({"success": False, "message": "Invalid Key"})
    
    key_data = db[key]
    
    if not key_data.get("active", False):
        return jsonify({"success": False, "message": "Key is inactive"})
    
    # فحص ربط الجهاز (HWID Binding)
    saved_hwid = key_data.get("hwid")
    
    if saved_hwid is None:
        # أول مرة يتم استخدام المفتاح، اربطه بهذا الجهاز فوراً!
        if hwid:
            key_data["hwid"] = hwid
            save_db(db)
        else:
            return jsonify({"success": False, "message": "HWID required for first activation"})
    elif saved_hwid != hwid:
        # محاولة استخدام المفتاح على جهاز شخص آخر!
        return jsonify({"success": False, "message": "Key is bound to another device"})
    
    # المفتاح صالح والجهاز مطابق
    return jsonify({"success": True, "message": "Access Granted"})

@app.route("/", methods=["GET"])
def home():
    return jsonify({"status": "Server is running securely!"})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
