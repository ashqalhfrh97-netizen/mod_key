import os
import json
import random
import string
from datetime import datetime, timedelta
from flask import Flask, request, jsonify, render_template_string

app = Flask(__name__)

# ملف تخزين المفاتيح
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
KEYS_FILE = os.path.join(BASE_DIR, "keys.json")

def load_db():
    if not os.path.exists(KEYS_FILE):
        return {}
    try:
        with open(KEYS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {}

def save_db(data):
    try:
        with open(KEYS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print("Save error:", e)

# 1. لوحة التحكم الرئيسية (تظهر لك المفاتيح والتحكم بها من المتصفح)
@app.route("/", methods=["GET"])
def admin_panel():
    db = load_db()
    
    # قالب HTML بسيط ومرتب للوحة التحكم
    html = """
    <!DOCTYPE html>
    <html lang="ar" dir="rtl">
    <head>
        <meta charset="UTF-8">
        <title>لوحة تحكم المفاتيح - AK TEAM</title>
        <style>
            body { font-family: Tahoma, sans-serif; background: #121212; color: #fff; padding: 20px; }
            h1 { color: #4CAF50; text-align: center; }
            .card { background: #1e1e1e; padding: 15px; margin-bottom: 20px; border-radius: 8px; box-shadow: 0 4px 8px rgba(0,0,0,0.3); }
            button { background: #4CAF50; color: white; border: none; padding: 10px 20px; border-radius: 5px; cursor: pointer; font-size: 16px; }
            button:hover { background: #45a049; }
            .delete-btn { background: #f44336; padding: 5px 10px; }
            .delete-btn:hover { background: #d32f2f; }
            table { width: 100%; border-collapse: collapse; margin-top: 15px; }
            th, td { border: 1px solid #333; padding: 10px; text-align: center; }
            th { background: #222; }
            select, input { padding: 8px; border-radius: 4px; border: 1px solid #444; background: #222; color: #fff; margin-right: 10px; }
        </style>
    </head>
    <body>
        <h1>لوحة تحكم مفاتيح المود - AK TEAM</h1>
        <div class="card">
            <h3>توليد مفتاح جديد</h3>
            <form action="/create_key" method="GET">
                <label>مدة الصلاحية:</label>
                <select name="days">
                    <option value="1">ساعة / يوم واحد (1 يوم)</option>
                    <option value="3">3 أيام</option>
                    <option value="7" selected>أسبوع (7 أيام)</option>
                    <option value="30">شهر (30 يوم)</option>
                    <option value="365">سنة كاملة</option>
                    <option value="0">دائم (بدون انتهاء)</option>
                </select>
                <button type="submit">توليد مفتاح</button>
            </form>
        </div>
        
        <div class="card">
            <h3>المفاتيح الحالية في النظام</h3>
            <table>
                <tr>
                    <th>المفتاح</th>
                    <th>الحالة</th>
                    <th>بصمة الجهاز (HWID)</th>
                    <th>تاريخ الانتهاء</th>
                    <th>إجراء</th>
                </tr>
                {% for key, data in keys.items() %}
                <tr>
                    <td><b>{{ key }}</b></td>
                    <td><span style="color: {{ 'green' if data.active else 'red' }};">{{ 'فعّال' if data.active else 'معطل' }}</span></td>
                    <td>{{ data.hwid if data.hwid else 'غير مرتبط' }}</td>
                    <td>{{ data.expires_at if data.expires_at else 'دائم' }}</td>
                    <td>
                        <a href="/delete?key={{ key }}"><button class="delete-btn">حذف</button></a>
                    </td>
                </tr>
                {% endfor %}
            </table>
        </div>
    </body>
    </html>
    """
    return render_template_string(html, keys=db)

# مسار توليد المفتاح من لوحة التحكم مع تحديد الأيام
@app.route("/create_key", methods=["GET"])
def create_key():
    try:
        days = int(request.args.get("days", 7))
    except:
        days = 7
        
    chars = string.ascii_uppercase + string.digits
    key = "AK-" + "".join(random.choices(chars, k=4)) + "-" + "".join(random.choices(chars, k=4)) + "-" + "".join(random.choices(chars, k=4))
    
    if days > 0:
        expires_at = (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    else:
        expires_at = None # دائم
        
    db = load_db()
    db[key] = {
        "hwid": None,
        "active": True,
        "expires_at": expires_at
    }
    save_db(db)
    
    return admin_panel()

# مسار حذف مفتاح من لوحة التحكم
@app.route("/delete", methods=["GET"])
def delete_key():
    key = request.args.get("key")
    db = load_db()
    if key in db:
        del db[key]
        save_db(db)
    return admin_panel()

# 2. مسار التحقق الأساسي للمود (Check API - يتأكد من المفتاح والـ HWID والصلاحية)
@app.route("/check", methods=["POST", "GET"])
def check_key():
    key = request.form.get("key") or request.args.get("key")
    hwid = request.form.get("hwid") or request.args.get("hwid")
    
    if not key:
        return jsonify({"success": False, "message": "No key provided"}), 400

    db = load_db()
    
    if key not in db:
        return jsonify({"success": False, "message": "Key not found"})
    
    key_data = db[key]
    
    if not key_data.get("active", False):
        return jsonify({"success": False, "message": "Key is inactive"})
    
    # فحص تاريخ الانتهاء (Expiry Check)
    expires_at = key_data.get("expires_at")
    if expires_at:
        exp_date = datetime.strptime(expires_at, "%Y-%m-%d %H:%M:%S")
        if datetime.now() > exp_date:
            return jsonify({"success": False, "message": "Key has expired"})

    # فحص ربط الجهاز (HWID Binding)
    saved_hwid = key_data.get("hwid")
    
    if saved_hwid is None:
        if hwid:
            key_data["hwid"] = hwid
            save_db(db)
        else:
            return jsonify({"success": False, "message": "HWID required for first activation"})
    elif saved_hwid != hwid:
        return jsonify({"success": False, "message": "Key is bound to another device"})
    
    return jsonify({"success": True, "message": "Key is valid"})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
