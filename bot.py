import os
import json
import random
import string
from datetime import datetime, timedelta
from flask import Flask, request, jsonify, render_template_string, session, redirect, url_for

app = Flask(__name__)
app.secret_key = "ak_team_secret_key_secure"

# بيانات الدخول للوحة التحكم (بدون رموز قد تسبب مشاكل بالمتصفح مثل الـ @)
ADMIN_USER = "X50ASD"
ADMIN_PASS = "basar2011"

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

# صفحة تسجيل الدخول والتحكم بنفس الملف لتجنب مشاكل الـ 404
@app.route("/", methods=["GET", "POST"])
def admin_panel():
    error = None
    
    # معالجة تسجيل الدخول إذا تم إرسال البيانات
    if request.method == "POST":
        action = request.form.get("action")
        
        if action == "login":
            username = request.form.get("username", "").strip()
            password = request.form.get("password", "").strip()
            if username == ADMIN_USER and password == ADMIN_PASS:
                session["logged_in"] = True
            else:
                error = "اسم المستخدم أو كلمة المرور غير صحيحة!"
                
        elif action == "logout":
            session.pop("logged_in", None)
            return redirect(url_for("admin_panel"))
            
        elif action == "create":
            if not session.get("logged_in"):
                return redirect(url_for("admin_panel"))
            
            preset = request.form.get("preset_days", "0")
            try:
                hours = float(request.form.get("custom_hours", 0))
                minutes = float(request.form.get("custom_minutes", 0))
            except:
                hours = 0
                minutes = 0
                
            total_delta = timedelta(0)
            is_permanent = False
            
            if preset == "permanent":
                is_permanent = True
            elif preset != "0":
                total_delta = timedelta(days=float(preset))
            elif hours > 0 or minutes > 0:
                total_delta = timedelta(hours=hours, minutes=minutes)
            else:
                total_delta = timedelta(days=7)
                
            chars = string.ascii_uppercase + string.digits
            key = "AK-" + "".join(random.choices(chars, k=4)) + "-" + "".join(random.choices(chars, k=4)) + "-" + "".join(random.choices(chars, k=4))
            
            if is_permanent:
                expires_at = None
            else:
                expires_at = (datetime.now() + total_delta).strftime("%Y-%m-%d %H:%M:%S")
                
            db = load_db()
            db[key] = {
                "hwid": None,
                "active": True,
                "expires_at": expires_at
            }
            save_db(db)
            return redirect(url_for("admin_panel"))
            
        elif action == "delete":
            if not session.get("logged_in"):
                return redirect(url_for("admin_panel"))
            
            key_to_delete = request.form.get("key")
            db = load_db()
            if key_to_delete in db:
                del db[key_to_delete]
                save_db(db)
            return redirect(url_for("admin_panel"))

    # إذا لم يكن مسجل دخول، اعرض صفحة تسجيل الدخول
    if not session.get("logged_in"):
        html_login = """
        <!DOCTYPE html>
        <html lang="ar" dir="rtl">
        <head>
            <meta charset="UTF-8">
            <title>تسجيل الدخول - AK TEAM</title>
            <style>
                body { font-family: Tahoma, sans-serif; background: linear-gradient(-45deg, #111, #222, #1a1a1a, #000); background-size: 400% 400%; animation: gradientBG 10s ease infinite; color: #fff; display: flex; justify-content: center; align-items: center; height: 100vh; margin: 0; }
                @keyframes gradientBG { 0% { background-position: 0% 50%; } 50% { background-position: 100% 50%; } 100% { background-position: 0% 50%; } }
                .login-card { background: rgba(30, 30, 30, 0.85); backdrop-filter: blur(10px); padding: 30px; border-radius: 12px; box-shadow: 0 8px 32px rgba(0,0,0,0.5); width: 320px; text-align: center; }
                h2 { color: #4CAF50; margin-bottom: 20px; }
                input { width: 90%; padding: 12px; margin: 10px 0; border-radius: 6px; border: 1px solid #444; background: #222; color: #fff; font-size: 14px; }
                button { background: #4CAF50; color: white; border: none; padding: 12px; width: 100%; border-radius: 6px; cursor: pointer; font-size: 16px; margin-top: 10px; font-weight: bold; }
                button:hover { background: #45a049; }
                .error { color: #f44336; font-size: 13px; margin-top: 10px; }
            </style>
        </head>
        <body>
            <div class="login-card">
                <h2>تسجيل دخول المشرف</h2>
                <form method="POST">
                    <input type="hidden" name="action" value="login">
                    <input type="text" name="username" placeholder="اسم المستخدم (X50ASD)" required>
                    <input type="password" name="password" placeholder="كلمة المرور" required>
                    <button type="submit">دخول</button>
                </form>
                {% if error %}<div class="error">{{ error }}</div>{% endif %}
            </div>
        </body>
        </html>
        """
        return render_template_string(html_login, error=error)

    # إذا كان مسجل دخول، اعرض لوحة التحكم
    db = load_db()
    html_panel = """
    <!DOCTYPE html>
    <html lang="ar" dir="rtl">
    <head>
        <meta charset="UTF-8">
        <title>لوحة تحكم المفاتيح - AK TEAM</title>
        <style>
            body { font-family: Tahoma, sans-serif; background: linear-gradient(-45deg, #0d0d0d, #1a1a1a, #111, #000); background-size: 400% 400%; animation: gradientBG 12s ease infinite; color: #fff; padding: 20px; margin: 0; min-height: 100vh; }
            @keyframes gradientBG { 0% { background-position: 0% 50%; } 50% { background-position: 100% 50%; } 100% { background-position: 0% 50%; } }
            .header { display: flex; justify-content: space-between; align-items: center; max-width: 900px; margin: 0 auto 20px auto; }
            h1 { color: #4CAF50; margin: 0; text-shadow: 0 0 10px rgba(76,175,80,0.3); }
            .logout-btn { background: #f44336; color: white; border: none; padding: 8px 15px; border-radius: 5px; cursor: pointer; font-size: 14px; }
            .card { background: rgba(30, 30, 30, 0.85); backdrop-filter: blur(8px); padding: 20px; margin: 0 auto 20px auto; max-width: 900px; border-radius: 10px; box-shadow: 0 8px 20px rgba(0,0,0,0.4); }
            button { background: #4CAF50; color: white; border: none; padding: 10px 20px; border-radius: 5px; cursor: pointer; font-size: 15px; font-weight: bold; }
            button:hover { background: #45a049; }
            .delete-btn { background: #f44336; padding: 5px 10px; font-size: 13px; }
            .delete-btn:hover { background: #d32f2f; }
            table { width: 100%; border-collapse: collapse; margin-top: 15px; }
            th, td { border: 1px solid #333; padding: 10px; text-align: center; font-size: 14px; }
            th { background: rgba(20,20,20,0.9); }
            select, input { padding: 9px; border-radius: 5px; border: 1px solid #444; background: #222; color: #fff; margin-left: 5px; margin-bottom: 10px; }
            .form-group { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; margin-top: 10px; }
        </style>
    </head>
    <body>
        <div class="header">
            <h1>لوحة تحكم مفاتيح المود - AK TEAM</h1>
            <form method="POST" style="margin:0;">
                <input type="hidden" name="action" value="logout">
                <button type="submit" class="logout-btn">تسجيل الخروج</button>
            </form>
        </div>
        
        <div class="card">
            <h3>توليد مفتاح جديد</h3>
            <form method="POST">
                <input type="hidden" name="action" value="create">
                <div class="form-group">
                    <label>المدة الجاهزة:</label>
                    <select name="preset_days">
                        <option value="0">اختر مدة جاهزة أو استخدم المخصص أدناه</option>
                        <option value="0.0416">ساعة واحدة (1 ساعة)</option>
                        <option value="0.1458">3 ساعات ونصف (3س و 30د)</option>
                        <option value="1">يوم واحد (1 يوم)</option>
                        <option value="3">3 أيام</option>
                        <option value="7">أسبوع (7 أيام)</option>
                        <option value="30">شهر (30 يوم)</option>
                        <option value="365">سنة كاملة</option>
                        <option value="permanent">دائم (بدون انتهاء)</option>
                    </select>
                </div>
                
                <div class="form-group">
                    <label>مخصص (ساعات ودقائق):</label>
                    ساعات: <input type="number" name="custom_hours" min="0" value="0" style="width: 70px;">
                    دقائق: <input type="number" name="custom_minutes" min="0" value="0" style="width: 70px;">
                    <button type="submit">توليد المفتاح</button>
                </div>
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
                        <form method="POST" style="margin:0;">
                            <input type="hidden" name="action" value="delete">
                            <input type="hidden" name="key" value="{{ key }}">
                            <button type="submit" class="delete-btn">حذف</button>
                        </form>
                    </td>
                </tr>
                {% endfor %}
            </table>
        </div>
    </body>
    </html>
    """
    return render_template_string(html_panel, keys=db)

# مسار التحقق الأساسي للمود
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
    
    # فحص تاريخ الانتهاء
    expires_at = key_data.get("expires_at")
    if expires_at:
        exp_date = datetime.strptime(expires_at, "%Y-%m-%d %H:%M:%S")
        if datetime.now() > exp_date:
            return jsonify({"success": False, "message": "Key has expired"})

    # فحص ربط الجهاز (HWID)
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
