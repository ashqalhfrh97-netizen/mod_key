import os
import json
import random
import string
from datetime import datetime, timedelta
from flask import Flask, request, jsonify, render_template_string, session, redirect, url_for

app = Flask(__name__)
app.secret_key = "ak_team_secret_key_secure"

# بيانات الدخول لوحة التحكم
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

@app.route("/", methods=["GET", "POST"])
def admin_panel():
    error = None
    
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

    if not session.get("logged_in"):
        html_login = """
        <!DOCTYPE html>
        <html lang="ar" dir="rtl">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>تسجيل الدخول - AK TEAM</title>
            <style>
                * { box-sizing: border-box; }
                body {
                    font-family: Tahoma, sans-serif;
                    background: linear-gradient(135deg, #0f0c29, #302b63, #24243e);
                    background-size: 400% 400%;
                    animation: gradientBG 10s ease infinite;
                    color: #fff;
                    display: flex;
                    justify-content: center;
                    align-items: center;
                    height: 100vh;
                    margin: 0;
                }
                @keyframes gradientBG {
                    0% { background-position: 0% 50%; }
                    50% { background-position: 100% 50%; }
                    100% { background-position: 0% 50%; }
                }
                .login-card {
                    background: rgba(20, 20, 30, 0.85);
                    backdrop-filter: blur(12px);
                    -webkit-backdrop-filter: blur(12px);
                    padding: 30px;
                    border-radius: 12px;
                    box-shadow: 0 8px 32px rgba(0,0,0,0.6);
                    width: 320px;
                    text-align: center;
                    border: 1px solid rgba(255,255,255,0.1);
                }
                h2 { color: #4CAF50; margin-bottom: 20px; }
                input {
                    width: 100%;
                    padding: 12px;
                    margin: 10px 0;
                    border-radius: 6px;
                    border: 1px solid #444;
                    background: #111;
                    color: #fff;
                    font-size: 14px;
                }
                button {
                    background: #4CAF50;
                    color: white;
                    border: none;
                    padding: 12px;
                    width: 100%;
                    border-radius: 6px;
                    cursor: pointer;
                    font-size: 16px;
                    margin-top: 10px;
                    font-weight: bold;
                    transition: 0.3s;
                }
                button:hover { background: #45a049; }
                .error { color: #ff5252; font-size: 13px; margin-top: 10px; }
            </style>
        </head>
        <body>
            <div class="login-card">
                <h2>تسجيل دخول المشرف</h2>
                <form method="POST">
                    <input type="hidden" name="action" value="login">
                    <input type="text" name="username" placeholder="اسم المستخدم" required>
                    <input type="password" name="password" placeholder="كلمة المرور" required>
                    <button type="submit">دخول</button>
                </form>
                {% if error %}<div class="error">{{ error }}</div>{% endif %}
            </div>
        </body>
        </html>
        """
        return render_template_string(html_login, error=error)

    db = load_db()
    html_panel = """
    <!DOCTYPE html>
    <html lang="ar" dir="rtl">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>لوحة تحكم المفاتيح - AK TEAM</title>
        <style>
            * { box-sizing: border-box; }
            body {
                font-family: Tahoma, sans-serif;
                background: linear-gradient(135deg, #0f0c29, #302b63, #24243e);
                background-size: 400% 400%;
                animation: gradientBG 15s ease infinite;
                color: #fff;
                padding: 20px;
                margin: 0;
                min-height: 100vh;
            }
            @keyframes gradientBG {
                0% { background-position: 0% 50%; }
                50% { background-position: 100% 50%; }
                100% { background-position: 0% 50%; }
            }
            .header {
                display: flex;
                justify-content: space-between;
                align-items: center;
                max-width: 900px;
                margin: 0 auto 20px auto;
                flex-wrap: wrap;
                gap: 10px;
            }
            h1 { color: #4CAF50; margin: 0; text-shadow: 0 0 10px rgba(76,175,80,0.4); font-size: 22px; }
            .logout-btn { background: #f44336; color: white; border: none; padding: 8px 15px; border-radius: 5px; cursor: pointer; font-size: 14px; }
            .card {
                background: rgba(20, 20, 30, 0.85);
                backdrop-filter: blur(12px);
                -webkit-backdrop-filter: blur(12px);
                padding: 20px;
                margin: 0 auto 20px auto;
                max-width: 900px;
                border-radius: 10px;
                box-shadow: 0 8px 25px rgba(0,0,0,0.5);
                border: 1px solid rgba(255,255,255,0.08);
                overflow-x: auto;
            }
            button { background: #4CAF50; color: white; border: none; padding: 10px 20px; border-radius: 5px; cursor: pointer; font-size: 15px; font-weight: bold; transition: 0.3s; }
            button:hover { background: #45a049; }
            .copy-btn { background: #2196F3; padding: 5px 10px; font-size: 12px; margin-top: 5px; }
            .copy-btn:hover { background: #0b7dda; }
            .delete-btn { background: #f44336; padding: 5px 10px; font-size: 13px; }
            .delete-btn:hover { background: #d32f2f; }
            table { width: 100%; border-collapse: collapse; margin-top: 15px; min-width: 600px; }
            th, td { border: 1px solid #333; padding: 10px; text-align: center; font-size: 14px; }
            th { background: rgba(15,15,25,0.95); color: #4CAF50; }
            select, input { padding: 9px; border-radius: 5px; border: 1px solid #444; background: #111; color: #fff; margin-left: 5px; margin-bottom: 10px; }
            .form-group { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; margin-top: 10px; }
        </style>
        <script>
            function copyKey(text) {
                navigator.clipboard.writeText(text).then(function() {
                    alert("تم نسخ المفتاح بنجاح: " + text);
                }, function(err) {
                    alert("فشل النسخ");
                });
            }
        </script>
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
                    <td>
                        <b>{{ key }}</b><br>
                        <button type="button" class="copy-btn" onclick="copyKey('{{ key }}')">نسخ</button>
                    </td>
                    <td><span style="color: {{ '#4CAF50' if data.active else '#f44336' }};">{{ 'فعّال' if data.active else 'معطل' }}</span></td>
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

# مسار التحقق للمود (رسائل عربية واضحة)
@app.route("/check", methods=["POST", "GET"])
def check_key():
    key = request.form.get("key") or request.args.get("key")
    hwid = request.form.get("hwid") or request.args.get("hwid")
    
    if not key:
        return jsonify({"success": False, "message": "الرجاء إدخال المفتاح"}), 400

    db = load_db()
    
    if key not in db:
        return jsonify({"success": False, "message": "المفتاح خطأ أو غير موجود"})
    
    key_data = db[key]
    
    if not key_data.get("active", False):
        return jsonify({"success": False, "message": "تم ايقاف هذا المفتاح"})
    
    expires_at = key_data.get("expires_at")
    if expires_at:
        exp_date = datetime.strptime(expires_at, "%Y-%m-%d %H:%M:%S")
        if datetime.now() > exp_date:
            return jsonify({"success": False, "message": "انتهت صلاحية المفتاح"})

    saved_hwid = key_data.get("hwid")
    
    if saved_hwid is None:
        if hwid:
            key_data["hwid"] = hwid
            save_db(db)
        else:
            return jsonify({"success": False, "message": "خطأ في بيانات الجهاز"})
    elif saved_hwid != hwid:
        return jsonify({"success": False, "message": "تم استخدام الكود على جهاز آخر"})
    
    return jsonify({"success": True, "message": "تم التفعيل بنجاح"})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
