import os
import json
import random
import string
from datetime import datetime, timedelta
from flask import Flask, request, jsonify, render_template_string, session, redirect, url_for

app = Flask(__name__)
app.secret_key = "ak_team_secret_key_secure"

# =========================
# بيانات دخول لوحة التحكم
# =========================
ADMIN_USER = "X50ASD"
ADMIN_PASS = "basar2011"

# =========================
# ملف تخزين المفاتيح
# =========================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
KEYS_FILE = os.path.join(BASE_DIR, "keys.json")


# =========================
# تحميل قاعدة البيانات
# =========================
def load_db():
    if not os.path.exists(KEYS_FILE):
        return {}

    try:
        with open(KEYS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)

        # حماية إذا كان الملف ليس Dictionary
        if not isinstance(data, dict):
            return {}

        return data

    except Exception as e:
        print("Load error:", e)
        return {}


# =========================
# حفظ قاعدة البيانات
# =========================
def save_db(data):
    try:
        temp_file = KEYS_FILE + ".tmp"

        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump(
                data,
                f,
                indent=2,
                ensure_ascii=False
            )

        os.replace(temp_file, KEYS_FILE)

    except Exception as e:
        print("Save error:", e)


# =========================
# JSON عربي
# =========================
def arabic_json(data, status=200):
    response = app.response_class(
        response=json.dumps(
            data,
            ensure_ascii=False
        ),
        status=status,
        mimetype="application/json"
    )

    return response


# =========================
# توليد مفتاح عشوائي
# =========================
def generate_key():
    chars = string.ascii_uppercase + string.digits

    while True:
        key = (
            "AK-"
            + "".join(random.choices(chars, k=4))
            + "-"
            + "".join(random.choices(chars, k=4))
            + "-"
            + "".join(random.choices(chars, k=4))
        )

        db = load_db()

        if key not in db:
            return key


# =========================
# تحويل المفاتيح القديمة
# =========================
def normalize_key_data(data):
    """
    يحول المفتاح القديم الذي يحتوي على hwid واحد
    إلى النظام الجديد الذي يستخدم hwids.
    """

    if not isinstance(data, dict):
        data = {}

    # إذا كان المفتاح قديم
    if "hwids" not in data:

        old_hwid = data.get("hwid")

        if old_hwid:
            data["hwids"] = [old_hwid]
        else:
            data["hwids"] = []

    # إزالة hwid القديم منطقياً
    # ونتركه لو أردت التوافق مع أي نسخة قديمة
    if "hwid" not in data:
        data["hwid"] = None

    # الحد الافتراضي للمفاتيح القديمة = جهاز واحد
    if "device_limit" not in data:

        if data.get("hwid"):
            data["device_limit"] = 1
        else:
            data["device_limit"] = 1

    # حماية
    try:
        data["device_limit"] = int(data["device_limit"])
    except:
        data["device_limit"] = 1

    if data["device_limit"] < 1:
        data["device_limit"] = 1

    if not isinstance(data.get("hwids"), list):
        data["hwids"] = []

    return data


# =========================
# لوحة التحكم
# =========================
@app.route("/", methods=["GET", "POST"])
def admin_panel():

    error = None
    success = None

    if request.method == "POST":

        action = request.form.get("action")

        # =====================
        # تسجيل الدخول
        # =====================
        if action == "login":

            username = request.form.get(
                "username",
                ""
            ).strip()

            password = request.form.get(
                "password",
                ""
            ).strip()

            if (
                username == ADMIN_USER
                and password == ADMIN_PASS
            ):
                session["logged_in"] = True

            else:
                error = "اسم المستخدم أو كلمة المرور غير صحيحة!"

        # =====================
        # تسجيل الخروج
        # =====================
        elif action == "logout":

            session.pop("logged_in", None)

            return redirect(
                url_for("admin_panel")
            )

        # =====================
        # إنشاء مفتاح
        # =====================
        elif action == "create":

            if not session.get("logged_in"):
                return redirect(
                    url_for("admin_panel")
                )

            # الكود المخصص
            custom_key = request.form.get(
                "custom_key",
                ""
            ).strip().upper()

            # عدد الأجهزة
            try:
                device_limit = int(
                    request.form.get(
                        "device_limit",
                        "1"
                    )
                )
            except:
                device_limit = 1

            # حماية
            if device_limit < 1:
                device_limit = 1

            # مدة جاهزة
            preset = request.form.get(
                "preset_days",
                "0"
            )

            # ساعات ودقائق
            try:
                hours = float(
                    request.form.get(
                        "custom_hours",
                        0
                    )
                )
            except:
                hours = 0

            try:
                minutes = float(
                    request.form.get(
                        "custom_minutes",
                        0
                    )
                )
            except:
                minutes = 0

            # =====================
            # تحديد المدة
            # =====================
            total_delta = timedelta(0)
            is_permanent = False

            if preset == "permanent":

                is_permanent = True

            elif preset != "0":

                try:
                    total_delta = timedelta(
                        days=float(preset)
                    )
                except:
                    total_delta = timedelta(days=7)

            elif hours > 0 or minutes > 0:

                total_delta = timedelta(
                    hours=hours,
                    minutes=minutes
                )

            else:

                # افتراضي أسبوع
                total_delta = timedelta(days=7)

            # =====================
            # إنشاء / اختيار الكود
            # =====================
            if custom_key:

                # السماح بحروف وأرقام و -
                allowed = (
                    string.ascii_uppercase
                    + string.digits
                    + "-"
                    + "_"
                )

                if not all(
                    c in allowed
                    for c in custom_key
                ):
                    error = (
                        "الكود يحتوي على رموز غير مسموحة!"
                    )

                elif len(custom_key) < 2:
                    error = (
                        "الكود يجب أن يحتوي على حرفين "
                        "أو أكثر!"
                    )

                else:

                    key = custom_key

            else:

                key = generate_key()

            # =====================
            # التأكد من عدم التكرار
            # =====================
            if not error:

                db = load_db()

                if key in db:

                    error = (
                        "هذا المفتاح موجود بالفعل!"
                    )

                else:

                    if is_permanent:

                        expires_at = None

                    else:

                        expires_at = (
                            datetime.now()
                            + total_delta
                        ).strftime(
                            "%Y-%m-%d %H:%M:%S"
                        )

                    db[key] = {
                        "hwid": None,

                        "hwids": [],

                        "device_limit": device_limit,

                        "active": True,

                        "expires_at": expires_at
                    }

                    save_db(db)

                    return redirect(
                        url_for("admin_panel")
                    )

        # =====================
        # حذف مفتاح
        # =====================
        elif action == "delete":

            if not session.get("logged_in"):
                return redirect(
                    url_for("admin_panel")
                )

            key_to_delete = request.form.get(
                "key"
            )

            db = load_db()

            if key_to_delete in db:

                del db[key_to_delete]

                save_db(db)

            return redirect(
                url_for("admin_panel")
            )

        # =====================
        # تفعيل / تعطيل مفتاح
        # =====================
        elif action == "toggle":

            if not session.get("logged_in"):
                return redirect(
                    url_for("admin_panel")
                )

            key_to_toggle = request.form.get(
                "key"
            )

            db = load_db()

            if key_to_toggle in db:

                db[key_to_toggle] = normalize_key_data(
                    db[key_to_toggle]
                )

                db[key_to_toggle]["active"] = not db[
                    key_to_toggle
                ].get("active", False)

                save_db(db)

            return redirect(
                url_for("admin_panel")
            )

    # =========================
    # صفحة تسجيل الدخول
    # =========================
    if not session.get("logged_in"):

        html_login = """
        <!DOCTYPE html>

        <html lang="ar" dir="rtl">

        <head>

            <meta charset="UTF-8">

            <meta
                name="viewport"
                content="width=device-width, initial-scale=1.0"
            >

            <title>تسجيل الدخول - AK TEAM</title>

            <style>

                * {
                    box-sizing: border-box;
                }

                body {

                    font-family: Tahoma, sans-serif;

                    background:
                    linear-gradient(
                        rgba(0,0,0,0.7),
                        rgba(0,0,0,0.7)
                    ),
                    url(
                        'https://images.unsplash.com/photo-1578632767115-351597cf2477?q=80&w=1000&auto=format&fit=crop'
                    );

                    background-size: cover;

                    background-position: center;

                    background-attachment: fixed;

                    color: #fff;

                    display: flex;

                    justify-content: center;

                    align-items: center;

                    height: 100vh;

                    margin: 0;
                }

                .login-card {

                    background:
                    rgba(20,20,30,0.85);

                    backdrop-filter:
                    blur(12px);

                    padding: 30px;

                    border-radius: 12px;

                    box-shadow:
                    0 8px 32px
                    rgba(0,0,0,0.8);

                    width: 320px;

                    text-align: center;

                    border:
                    1px solid
                    rgba(255,255,255,0.15);
                }

                h2 {

                    color: #4CAF50;

                    margin-bottom: 20px;
                }

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
                }

                .error {

                    color: #ff5252;

                    font-size: 13px;

                    margin-top: 10px;
                }

            </style>

        </head>

        <body>

            <div class="login-card">

                <h2>
                    تسجيل دخول المشرف
                </h2>

                <form method="POST">

                    <input
                        type="hidden"
                        name="action"
                        value="login"
                    >

                    <input
                        type="text"
                        name="username"
                        placeholder="اسم المستخدم"
                        required
                    >

                    <input
                        type="password"
                        name="password"
                        placeholder="كلمة المرور"
                        required
                    >

                    <button type="submit">
                        دخول
                    </button>

                </form>

                {% if error %}

                <div class="error">
                    {{ error }}
                </div>

                {% endif %}

            </div>

        </body>

        </html>
        """

        return render_template_string(
            html_login,
            error=error
        )

    # =========================
    # لوحة التحكم
    # =========================
    db = load_db()

    # ترتيب / تحويل البيانات
    for k in list(db.keys()):

        db[k] = normalize_key_data(
            db[k]
        )

    save_db(db)

    html_panel = """

    <!DOCTYPE html>

    <html lang="ar" dir="rtl">

    <head>

        <meta charset="UTF-8">

        <meta
            name="viewport"
            content="width=device-width, initial-scale=1.0"
        >

        <title>
            لوحة تحكم المفاتيح - AK TEAM
        </title>

        <style>

            * {
                box-sizing: border-box;
            }

            body {

                font-family: Tahoma, sans-serif;

                background:
                linear-gradient(
                    rgba(0,0,0,0.75),
                    rgba(0,0,0,0.75)
                ),
                url(
                    'https://images.unsplash.com/photo-1578632767115-351597cf2477?q=80&w=1000&auto=format&fit=crop'
                );

                background-size: cover;

                background-position: center;

                background-attachment: fixed;

                color: #fff;

                padding: 20px;

                margin: 0;

                min-height: 100vh;
            }

            .header {

                display: flex;

                justify-content: space-between;

                align-items: center;

                max-width: 1000px;

                margin: 0 auto 20px auto;

                flex-wrap: wrap;

                gap: 10px;
            }

            h1 {

                color: #4CAF50;

                margin: 0;

                font-size: 22px;
            }

            .logout-btn {

                background: #f44336;

                color: white;

                border: none;

                padding: 8px 15px;

                border-radius: 5px;

                cursor: pointer;
            }

            .card {

                background:
                rgba(20,20,30,0.88);

                backdrop-filter:
                blur(12px);

                padding: 20px;

                margin:
                0 auto 20px auto;

                max-width: 1000px;

                border-radius: 10px;

                box-shadow:
                0 8px 25px
                rgba(0,0,0,0.7);

                border:
                1px solid
                rgba(255,255,255,0.12);

                overflow-x: auto;
            }

            button {

                background: #4CAF50;

                color: white;

                border: none;

                padding: 10px 20px;

                border-radius: 5px;

                cursor: pointer;

                font-size: 15px;

                font-weight: bold;
            }

            .copy-btn {

                background: #2196F3;

                padding: 5px 10px;

                font-size: 12px;

                margin-top: 5px;
            }

            .delete-btn {

                background: #f44336;

                padding: 5px 10px;

                font-size: 13px;
            }

            .toggle-btn {

                background: #ff9800;

                padding: 5px 10px;

                font-size: 13px;

                margin-bottom: 5px;
            }

            table {

                width: 100%;

                border-collapse: collapse;

                margin-top: 15px;

                min-width: 850px;
            }

            th,
            td {

                border:
                1px solid #444;

                padding: 10px;

                text-align: center;

                font-size: 13px;
            }

            th {

                background:
                rgba(15,15,25,0.95);

                color: #4CAF50;
            }

            select,
            input {

                padding: 9px;

                border-radius: 5px;

                border:
                1px solid #444;

                background: #111;

                color: #fff;

                margin-left: 5px;

                margin-bottom: 10px;
            }

            .form-group {

                display: flex;

                gap: 10px;

                align-items: center;

                flex-wrap: wrap;

                margin-top: 10px;
            }

            .device-box {

                background: #111;

                padding: 5px 9px;

                border-radius: 5px;

                display: inline-block;
            }

            .green {
                color: #4CAF50;
            }

            .red {
                color: #f44336;
            }

        </style>

        <script>

            function copyKey(text) {

                navigator.clipboard
                .writeText(text)
                .then(function() {

                    alert(
                        "تم نسخ المفتاح بنجاح: "
                        + text
                    );

                })
                .catch(function() {

                    alert("فشل النسخ");

                });

            }

        </script>

    </head>

    <body>

        <div class="header">

            <h1>
                لوحة تحكم مفاتيح المود - AK TEAM
            </h1>

            <form
                method="POST"
                style="margin:0;"
            >

                <input
                    type="hidden"
                    name="action"
                    value="logout"
                >

                <button
                    type="submit"
                    class="logout-btn"
                >
                    تسجيل الخروج
                </button>

            </form>

        </div>


        <!-- =====================
             إنشاء مفتاح
        ====================== -->

        <div class="card">

            <h3>
                توليد مفتاح جديد
            </h3>

            <form method="POST">

                <input
                    type="hidden"
                    name="action"
                    value="create"
                >

                <div class="form-group">

                    <label>
                        الكود المخصص:
                    </label>

                    <input
                        type="text"
                        name="custom_key"
                        placeholder="مثال: EX25"
                        maxlength="64"
                        style="width:180px;"
                    >

                    <span>
                        اتركه فارغاً للتوليد التلقائي
                    </span>

                </div>


                <div class="form-group">

                    <label>
                        عدد الأجهزة:
                    </label>

                    <input
                        type="number"
                        name="device_limit"
                        min="1"
                        value="1"
                        required
                        style="width:120px;"
                    >

                    <span>
                        مثال: 20 = يسمح لـ 20 جهاز
                    </span>

                </div>


                <div class="form-group">

                    <label>
                        المدة الجاهزة:
                    </label>

                    <select name="preset_days">

                        <option value="0">
                            اختر مدة جاهزة
                        </option>

                        <option value="0.0416">
                            ساعة واحدة
                        </option>

                        <option value="0.1458">
                            3 ساعات ونصف
                        </option>

                        <option value="1">
                            يوم واحد
                        </option>

                        <option value="3">
                            3 أيام
                        </option>

                        <option value="7">
                            أسبوع
                        </option>

                        <option value="30">
                            شهر
                        </option>

                        <option value="365">
                            سنة
                        </option>

                        <option value="permanent">
                            دائم
                        </option>

                    </select>

                </div>


                <div class="form-group">

                    <label>
                        مخصص:
                    </label>

                    ساعات:

                    <input
                        type="number"
                        name="custom_hours"
                        min="0"
                        value="0"
                        style="width:80px;"
                    >

                    دقائق:

                    <input
                        type="number"
                        name="custom_minutes"
                        min="0"
                        value="0"
                        style="width:80px;"
                    >

                </div>


                <button type="submit">

                    توليد المفتاح

                </button>

            </form>

        </div>


        <!-- =====================
             المفاتيح
        ====================== -->

        <div class="card">

            <h3>
                المفاتيح الحالية
            </h3>

            <table>

                <tr>

                    <th>
                        المفتاح
                    </th>

                    <th>
                        الحالة
                    </th>

                    <th>
                        الأجهزة
                    </th>

                    <th>
                        تاريخ الانتهاء
                    </th>

                    <th>
                        إجراء
                    </th>

                </tr>


                {% for key, data in keys.items() %}

                <tr>

                    <td>

                        <b>
                            {{ key }}
                        </b>

                        <br>

                        <button
                            type="button"
                            class="copy-btn"
                            onclick="copyKey('{{ key }}')"
                        >
                            نسخ
                        </button>

                    </td>


                    <td>

                        {% if data.active %}

                            <span class="green">
                                فعّال
                            </span>

                        {% else %}

                            <span class="red">
                                معطل
                            </span>

                        {% endif %}

                    </td>


                    <td>

                        <span class="device-box">

                            {{ data.hwids|length }}

                            /

                            {{ data.device_limit }}

                        </span>

                        <br>

                        {% if data.hwids|length >= data.device_limit %}

                            <small class="red">
                                الحد مكتمل
                            </small>

                        {% else %}

                            <small class="green">
                                متاح
                            </small>

                        {% endif %}

                    </td>


                    <td>

                        {% if data.expires_at %}

                            {{ data.expires_at }}

                        {% else %}

                            <span class="green">
                                دائم
                            </span>

                        {% endif %}

                    </td>


                    <td>

                        <form
                            method="POST"
                            style="margin:0 0 5px 0;"
                        >

                            <input
                                type="hidden"
                                name="action"
                                value="toggle"
                            >

                            <input
                                type="hidden"
                                name="key"
                                value="{{ key }}"
                            >

                            <button
                                type="submit"
                                class="toggle-btn"
                            >

                                {% if data.active %}
                                    تعطيل
                                {% else %}
                                    تفعيل
                                {% endif %}

                            </button>

                        </form>


                        <form
                            method="POST"
                            style="margin:0;"
                        >

                            <input
                                type="hidden"
                                name="action"
                                value="delete"
                            >

                            <input
                                type="hidden"
                                name="key"
                                value="{{ key }}"
                            >

                            <button
                                type="submit"
                                class="delete-btn"
                            >
                                حذف
                            </button>

                        </form>

                    </td>

                </tr>

                {% endfor %}

            </table>

        </div>

    </body>

    </html>

    """

    return render_template_string(
        html_panel,
        keys=db
    )


# =========================================================
# التحقق من المفتاح
# =========================================================
@app.route(
    "/check",
    methods=["POST", "GET"]
)
def check_key():

    key = (
        request.form.get("key")
        or request.args.get("key")
        or ""
    ).strip().upper()

    hwid = (
        request.form.get("hwid")
        or request.args.get("hwid")
        or ""
    ).strip()

    # =========================
    # التحقق من البيانات
    # =========================
    if not key:

        return arabic_json(
            {
                "success": False,
                "code": "MISSING_KEY",
                "message": "الرجاء إدخال المفتاح"
            },
            400
        )

    if not hwid:

        return arabic_json(
            {
                "success": False,
                "code": "MISSING_HWID",
                "message": "خطأ في بيانات الجهاز"
            },
            400
        )

    # =========================
    # تحميل DB
    # =========================
    db = load_db()

    # =========================
    # المفتاح غير موجود
    # =========================
    if key not in db:

        return arabic_json(
            {
                "success": False,
                "code": "INVALID_KEY",
                "message": "المفتاح خطأ أو غير موجود"
            }
        )

    # =========================
    # تجهيز بيانات المفتاح
    # =========================
    key_data = normalize_key_data(
        db[key]
    )

    # =========================
    # حالة المفتاح
    # =========================
    if not key_data.get(
        "active",
        False
    ):

        return arabic_json(
            {
                "success": False,
                "code": "DISABLED",
                "message": "تم ايقاف هذا المفتاح"
            }
        )

    # =========================
    # انتهاء الصلاحية
    # =========================
    expires_at = key_data.get(
        "expires_at"
    )

    if expires_at:

        try:

            exp_date = datetime.strptime(
                expires_at,
                "%Y-%m-%d %H:%M:%S"
            )

            if datetime.now() >= exp_date:

                return arabic_json(
                    {
                        "success": False,
                        "code": "EXPIRED",
                        "message": "انتهت صلاحية المفتاح"
                    }
                )

        except Exception:

            return arabic_json(
                {
                    "success": False,
                    "code": "INVALID_EXPIRY",
                    "message": "خطأ في تاريخ صلاحية المفتاح"
                },
                500
            )

    # =========================
    # قائمة الأجهزة
    # =========================
    hwids = key_data.get(
        "hwids",
        []
    )

    # إزالة القيم الفارغة والتكرار
    hwids = list(
        dict.fromkeys(
            str(x).strip()
            for x in hwids
            if str(x).strip()
        )
    )

    # =========================
    # الجهاز مسجل مسبقاً
    # =========================
    if hwid in hwids:

        db[key]["hwids"] = hwids

        save_db(db)

        return arabic_json(
            {
                "success": True,
                "code": "ALREADY_REGISTERED",
                "message": "تم التفعيل بنجاح",
                "device_count": len(hwids),
                "device_limit": key_data["device_limit"]
            }
        )

    # =========================
    # التحقق من عدد الأجهزة
    # =========================
    device_limit = key_data.get(
        "device_limit",
        1
    )

    try:
        device_limit = int(
            device_limit
        )
    except:
        device_limit = 1

    if device_limit < 1:
        device_limit = 1

    # =========================
    # الحد الأقصى وصل
    # =========================
    if len(hwids) >= device_limit:

        return arabic_json(
            {
                "success": False,
                "code": "DEVICE_LIMIT",
                "message": "تم الوصول للحد الأقصى للأجهزة لهذا المفتاح",
                "device_count": len(hwids),
                "device_limit": device_limit
            }
        )

    # =========================
    # إضافة الجهاز الجديد
    # =========================
    hwids.append(hwid)

    db[key]["hwids"] = hwids

    # للتوافق مع النسخ القديمة
    if len(hwids) == 1:
        db[key]["hwid"] = hwids[0]
    else:
        db[key]["hwid"] = None

    db[key]["device_limit"] = device_limit

    save_db(db)

    # =========================
    # نجاح
    # =========================
    return arabic_json(
        {
            "success": True,
            "code": "ACTIVATED",
            "message": "تم التفعيل بنجاح",
            "device_count": len(hwids),
            "device_limit": device_limit
        }
    )


# =========================
# تشغيل السيرفر
# =========================
if __name__ == "__main__":

    port = int(
        os.environ.get(
            "PORT",
            8080
        )
    )

    app.run(
        host="0.0.0.0",
        port=port
        )
