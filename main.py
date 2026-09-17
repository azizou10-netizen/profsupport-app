import flet as ft
import sqlite3
import json
import os
import shutil
from pathlib import Path
from datetime import datetime, date, timedelta

# على الهاتف (أندرويد/iOS)، مجلد الكود نفسه غالبًا للقراءة فقط، وأي محاولة
# للكتابة فيه (قاعدة البيانات، الإعدادات، النسخ الاحتياطية) قد تفشل بصمت
# أو تُفقد البيانات عند كل إعادة فتح للتطبيق. لذلك نستخدم مجلد بيانات
# التطبيق الرسمي الذي توفّره Flet (دائم وقابل للكتابة دومًا)، ونرجع لمجلد
# الكود فقط أثناء التطوير على الحاسوب حيث لا يكون هذا المتغير معرَّفًا.
# متغيّر عام يحمل أي خطأ حدث أثناء التهيئة المبكرة (قبل ظهور أي شاشة).
# إن بقي None فكل شيء تمّ بسلام، وإلا فسنعرضه نصيًا للمستخدم بدل شاشة سوداء صامتة.
STARTUP_ERROR = None

try:
    _FLET_DATA_DIR = os.getenv("FLET_APP_STORAGE_DATA")
    APP_DIR = Path(_FLET_DATA_DIR) if _FLET_DATA_DIR else Path(__file__).resolve().parent
    APP_DIR.mkdir(parents=True, exist_ok=True)

    DB_PATH = APP_DIR / "tutor_data.db"
    SETTINGS_PATH = APP_DIR / "tutor_settings.json"
    BACKUP_DIR = APP_DIR / "backups"
except Exception:
    import traceback
    STARTUP_ERROR = "خطأ أثناء تجهيز مجلد البيانات:\n" + traceback.format_exc()
    # قيم احتياطية حتى لا ينهار الاستيراد كليًا
    APP_DIR = Path(__file__).resolve().parent
    DB_PATH = APP_DIR / "tutor_data.db"
    SETTINGS_PATH = APP_DIR / "tutor_settings.json"
    BACKUP_DIR = APP_DIR / "backups"

TRANSLATIONS = {
    "ar": {
        "app_title": "ProfSupport",
        "dashboard": "لوحة التحكم",
        "students": "إدارة التلاميذ",
        "groups": "إدارة المجموعات",
        "attendance": "تسجيل الحضور",
        "payments": "المدفوعات",
        "reports": "التقارير",
        "settings": "الإعدادات",
        "teacher": "الأستاذ(ة)",
        "save": "💾 حفظ",
        "back": "🔙 العودة",
        "back_home": "🔙 العودة للرئيسية",
        "edit": "✏️ تعديل",
        "add": "➕ إضافة",
        "delete": "❌ حذف",
        "name": "الاسم واللقب",
        "level": "المستوى الدراسي",
        "phone": "رقم الهاتف",
        "subject": "المادة الدراسية",
        "price": "السعر (دج)",
        "group": "المجموعة",
        "select_groups": "اختر المجموعات المسجل فيها:",
        "today_payments": "مدفوعات اليوم",
        "month_payments": "مدفوعات الشهر",
        "total_payments": "المجموع الإجمالي",
        "debts": "إجمالي الديون",
        "recent_payments": "آخر الدفعات",
        "today_attendance": "حضور وغياب اليوم",
        "present_today": "✅ حضور اليوم",
        "absent_today": "❌ غياب اليوم",
        "all_groups_report": "📊 تقرير كل المجموعات",
        "single_group_report": "📋 تقرير مجموعة محدودة",
        "lang_setting": "لغة البرنامج",
        "theme_setting": "المظهر",
        "currency": "العملة",
        "unassigned_students": "تلاميذ بدون مجموعة",
        "student_count": "عدد التلاميذ",
        "group_count": "عدد المجموعات",
        "saved_success": "تم الحفظ بنجاح.",
        "error_fields": "يرجى ملء جميع الحقول المطلوبة.",
        "present": "حاضر",
        "absent": "غائب",
        "amount": "المبلغ",
        "date": "التاريخ",
        "payment_type": "نوع الدفع",
        "notes": "ملاحظات",
        "backup": "💾 إنشاء نسخة احتياطية",
    }
}

# قيم ثابتة تُخزَّن في قاعدة البيانات، منفصلة عن لغة العرض (t())
# هذا يمنع مشكلة اختلاف النص المخزَّن حسب اللغة المختارة وقت التسجيل
STATUS_PRESENT = "حاضر"
STATUS_PRESENT_UNPAID = "حاضر ولم يدفع"  # قيمة قديمة، تُقرأ فقط لتوافق البيانات السابقة
STATUS_ABSENT = "غائب"

SESSIONS_PER_PACKAGE = 4  # عدد الحصص في كل "باقة" دفع (حسب طلب الأستاذ)

def get_db_connection():
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA foreign_keys = ON")
    return conn

def compute_debt_map(conn):
    """
    يحسب دَين كل (تلميذ، مجموعة) على نموذج "باقات الحصص":
    الدَّين = (عدد الحصص التي حضرها التلميذ في هذه المجموعة × سعر الحصة الواحدة) - مجموع ما دفعه.
    سعر الحصة الواحدة = سعر المجموعة ÷ SESSIONS_PER_PACKAGE.
    الغياب لا يُحتسب إطلاقًا ضمن عدد الحصص، فلا يُنشئ أي دَين.
    يُرجع dict مفتاحه (student_id, group_id) وقيمته الدَّين (أو صفر إن لم يوجد).
    """
    group_prices = dict(conn.execute("SELECT id, price FROM groups").fetchall())
    default_amount = float(APP_SETTINGS.get("default_amount") or 0)

    attended = conn.execute(
        "SELECT student_id, group_id, COUNT(*) FROM attendance "
        "WHERE status != ? GROUP BY student_id, group_id",
        (STATUS_ABSENT,)
    ).fetchall()
    paid = dict(
        ((sid, gid), amt) for sid, gid, amt in conn.execute(
            "SELECT student_id, group_id, COALESCE(SUM(amount),0) FROM payments "
            "GROUP BY student_id, group_id"
        ).fetchall()
    )

    debts = {}
    for sid, gid, attended_cnt in attended:
        price = group_prices.get(gid)
        # ملاحظة مهمة: نستخدم `is not None` وليس فقط `if price` لأن سعرًا فعليًا
        # يساوي 0 (مجموعة مجانية) يُعتبر Falsy في بايثون، وكان يُستبدل خطأً
        # بالمبلغ الافتراضي فيُحتسب دَين وهمي على تلاميذ مجموعة مجانية.
        price = float(price) if price is not None else default_amount
        price_per_session = price / SESSIONS_PER_PACKAGE if price else 0
        total_paid = paid.get((sid, gid), 0) or 0
        debt = round(attended_cnt * price_per_session - total_paid, 2)
        debts[(sid, gid)] = max(0.0, debt)
    return debts

def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS groups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            level TEXT,
            price REAL DEFAULT 0,
            subject TEXT
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS students (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            level TEXT,
            phone TEXT,
            parent_name TEXT,
            parent_phone TEXT,
            group_id INTEGER,
            notes TEXT
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS student_groups (
            student_id INTEGER NOT NULL,
            group_id INTEGER NOT NULL,
            PRIMARY KEY (student_id, group_id),
            FOREIGN KEY (student_id) REFERENCES students(id) ON DELETE CASCADE,
            FOREIGN KEY (group_id) REFERENCES groups(id) ON DELETE CASCADE
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS attendance (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id INTEGER NOT NULL,
            group_id INTEGER,
            date TEXT NOT NULL,
            status TEXT NOT NULL,
            amount REAL DEFAULT 0,
            FOREIGN KEY (student_id) REFERENCES students(id),
            FOREIGN KEY (group_id) REFERENCES groups(id)
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id INTEGER NOT NULL,
            group_id INTEGER,
            amount REAL NOT NULL DEFAULT 0,
            payment_date TEXT NOT NULL,
            payment_type TEXT DEFAULT 'شهري',
            notes TEXT,
            FOREIGN KEY (student_id) REFERENCES students(id),
            FOREIGN KEY (group_id) REFERENCES groups(id)
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    conn.commit()
    conn.close()

if STARTUP_ERROR is None:
    try:
        init_db()
    except Exception:
        import traceback
        STARTUP_ERROR = "خطأ أثناء إنشاء قاعدة البيانات:\n" + traceback.format_exc()

def load_settings():
    defaults = {
        "teacher_name": "",
        "center_name": "",
        "default_amount": "500",
        "currency": "دج",
        "lang": "ar",
    }
    try:
        if SETTINGS_PATH.exists():
            saved = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
            defaults.update({k: str(v) for k, v in saved.items()})
    except Exception:
        pass
    return defaults

APP_SETTINGS = load_settings()

def save_settings_file(values):
    global APP_SETTINGS
    APP_SETTINGS = values.copy()
    SETTINGS_PATH.write_text(
        json.dumps(APP_SETTINGS, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

def t(key):
    lang = APP_SETTINGS.get("lang", "ar")
    return TRANSLATIONS.get(lang, TRANSLATIONS["ar"]).get(key, key)

def money(value):
    try:
        return f"{float(value or 0):,.2f}"
    except Exception:
        return "0.00"

def main(page: ft.Page):
    page.title = "إدارة دروس الدعم"
    page.rtl = True
    page.bgcolor = "#F8FAFC"
    page.theme_mode = ft.ThemeMode.LIGHT

    # إن حدث خطأ أثناء التهيئة المبكرة (قبل هذه النقطة)، نعرضه فورًا
    # ونوقف هنا، بدل أن نكمل ونخاطر بشاشة سوداء صامتة لاحقًا.
    if STARTUP_ERROR is not None:
        page.add(
            ft.Column(
                [
                    ft.Text("⚠️ حدث خطأ أثناء بدء التطبيق", size=18, weight=ft.FontWeight.BOLD, color="red"),
                    ft.Text(STARTUP_ERROR, size=12, color="#64748B", selectable=True),
                ],
                spacing=10,
                scroll=ft.ScrollMode.AUTO,
            )
        )
        return

    # ضبط أيقونة نافذة التطبيق من مجلد assets (icon.png). نجرّب أكثر من طريقة
    # لأن اسم الخاصية تغيّر بين إصدارات Flet المختلفة (مثل window_close أعلاه).
    for set_icon in (
        lambda: setattr(page.window, "icon", "icon.png"),
        lambda: setattr(page, "window_icon", "icon.png"),
    ):
        try:
            set_icon()
            break
        except Exception:
            continue

    def responsive_width(desired=500):
        """
        يحسب عرضًا يناسب الشاشة الفعلية بدل قيمة ثابتة.
        على الحاسوب (نافذة عريضة) يُستخدم العرض المطلوب (desired) كما هو.
        على الهاتف (شاشة أضيق) يُصغَّر العرض تلقائيًا ليدخل ضمن حدود الشاشة،
        بدل أن يُقتطع جزء من الواجهة كما كان يحدث سابقًا.
        """
        try:
            pw = page.width
        except Exception:
            pw = None
        if not pw:
            return desired
        return max(280, min(desired, pw - 24))

    # شاشة واحدة دائمة (بدل مكدّس متعدد الشاشات) — نغيّر محتواها فقط عند التنقل.
    # هذا يتفادى نهائيًا أي عملية push/pop داخل نظام الملاحة في Flutter،
    # وهي السبب الحقيقي وراء الشاشة السوداء التي كانت تظهر عند "الرجوع"
    # على أندرويد (أي تغيير في عدد الشاشات ضمن page.views كان يُحدث الخلل،
    # سواء بالحذف الكامل clear() أو بالحذف الجزئي).
    main_view = ft.View(
        controls=[],
        bgcolor="#F8FAFC",
        scroll=ft.ScrollMode.AUTO
    )
    page.views.append(main_view)

    # FilePicker واحد يُنشأ مرة واحدة فقط لكل الجلسة (بدل إنشائه في كل مرة تُفتح
    # فيها شاشة الإعدادات، وهو ما كان سيراكم عناصر overlay بلا داعٍ). يُستخدم
    # لحفظ النسخة الاحتياطية في مكان يختاره المستخدم فعليًا (مثل Downloads)
    # ولاستيراد نسخة سابقة، لأن المجلد الداخلي للتطبيق على أندرويد غير قابل
    # للوصول من مدير الملفات العادي.
    backup_file_picker = ft.FilePicker()
    page.overlay.append(backup_file_picker)

    def navigate(content, is_home=False):
        main_view.controls = [ft.Row([content], alignment=ft.MainAxisAlignment.CENTER)]
        page.update()

    # ملاحظة: تم إلغاء ربط زر/إيماءة الرجوع في أندرويد (page.on_view_pop) بناءً
    # على طلب صريح، لأنه لم يكن يعمل بشكل سليم على الجهاز. التنقّل للخلف الآن
    # يتم فقط عبر الأزرار الصريحة داخل التطبيق (مثل "🏠 العودة للرئيسية").

    def go_home(_=None):
        show_main_view()

    def close_app(_=None):
        """يغلق التطبيق نهائيًا بعد تأكيد المستخدم (متوافق مع إصدارات Flet المختلفة)."""
        def do_close():
            for attempt in (
                lambda: page.window.close(),
                lambda: page.window_close(),
                lambda: page.window_destroy(),
            ):
                try:
                    attempt()
                    return
                except Exception:
                    continue
            try:
                import os
                os._exit(0)
            except Exception:
                pass

        confirm_close_app("هل أنت متأكد من إغلاق البرنامج؟", do_close)

    def get_study_levels():
        return [
            ft.dropdown.Option("سنة أولى متوسط"),
            ft.dropdown.Option("سنة ثانية متوسط"),
            ft.dropdown.Option("سنة ثالثة متوسط"),
            ft.dropdown.Option("سنة رابعة متوسط"),
            ft.dropdown.Option("سنة أولى ثانوي"),
            ft.dropdown.Option("سنة ثانية ثانوي"),
            ft.dropdown.Option("سنة ثالثة ثانوي"),
        ]

    def get_subjects_list():
        return [
            ft.dropdown.Option("الرياضيات"),
            ft.dropdown.Option("الفيزياء"),
            ft.dropdown.Option("اللغة العربية"),
            ft.dropdown.Option("اللغة الفرنسية"),
            ft.dropdown.Option("اللغة الإنجليزية"),
            ft.dropdown.Option("علوم الطبيعة والحياة"),
        ]

    def group_options():
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT id, name, subject, level, price FROM groups ORDER BY name COLLATE NOCASE")
        rows = cur.fetchall()
        conn.close()
        return [
            ft.dropdown.Option(
                key=str(g[0]),
                text=f"{g[1]} - {g[2] or ''} ({g[3] or ''})"
            )
            for g in rows
        ], rows

    def create_stat_card(title_text, value, emoji_symbol):
        return ft.Container(
            expand=True, padding=12, bgcolor="white", border_radius=14,
            content=ft.Column(
                [
                    ft.Text(emoji_symbol, size=24),
                    ft.Text(str(value), size=20, weight=ft.FontWeight.BOLD),
                    ft.Text(title_text, size=12, color="#64748B", text_align=ft.TextAlign.CENTER),
                ],
                alignment=ft.MainAxisAlignment.CENTER,
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                spacing=2,
            ),
        )

    def create_btn(text, on_click, bgcolor="#2563EB", color="white"):
        def safe_click(e):
            """
            يلتقط أي خطأ غير متوقع يحدث عند الضغط على أي زر تنقّل في التطبيق
            ويعرضه في رسالة واضحة بدل أن يتسبب في إغلاق التطبيق فجأة (شاشة
            بيضاء تختفي)، وهذا يسهّل معرفة سبب أي عطل مستقبلي فورًا.
            """
            try:
                on_click(e)
            except Exception as ex:
                import traceback
                traceback.print_exc()
                show_error_snack(f"⚠️ حدث خطأ غير متوقع: {ex}")

        return ft.Container(
            width=responsive_width(),
            content=ft.ElevatedButton(
                text,
                on_click=safe_click,
                style=ft.ButtonStyle(
                    bgcolor=bgcolor, color=color, padding=12,
                    shape=ft.RoundedRectangleBorder(radius=8),
                ),
            )
        )

    def show_dialog(dlg):
        try:
            page.open(dlg)
        except AttributeError:
            if dlg not in page.overlay:
                page.overlay.append(dlg)
            dlg.open = True
            page.update()

    def close_dialog(dlg):
        dlg.open = False
        page.update()

    def show_error_snack(message):
        page.snack_bar = ft.SnackBar(content=ft.Text(message), bgcolor="red")
        page.snack_bar.open = True
        page.update()

    def confirm_delete(message, on_confirm):
        """يعرض مربع حوار تأكيد قبل تنفيذ أي عملية حذف."""
        dlg = ft.AlertDialog(
            modal=True,
            title=ft.Text("تأكيد الحذف"),
            content=ft.Text(message),
            actions=[
                ft.TextButton("إلغاء", on_click=lambda e: close_dialog(dlg)),
                ft.TextButton(
                    "❌ حذف نهائيًا",
                    style=ft.ButtonStyle(color="red"),
                    on_click=lambda e: (close_dialog(dlg), on_confirm()),
                ),
            ],
        )
        show_dialog(dlg)

    def confirm_close_app(message, on_confirm):
        """يعرض مربع حوار تأكيد مخصص لإغلاق البرنامج (بديل عن حوار الحذف العام)."""
        dlg = ft.AlertDialog(
            modal=True,
            title=ft.Text("تأكيد الإغلاق"),
            content=ft.Text(message),
            actions=[
                ft.TextButton("↩️ العودة", on_click=lambda e: close_dialog(dlg)),
                ft.TextButton(
                    "🔴 نعم، إغلاق",
                    style=ft.ButtonStyle(color="red"),
                    on_click=lambda e: (close_dialog(dlg), on_confirm()),
                ),
            ],
        )
        show_dialog(dlg)

    # -------------------- المجموعات --------------------
    def open_groups_screen(_=None):
        name = ft.TextField(label="اسم المجموعة", expand=True)
        subject = ft.Dropdown(label=t("subject"), expand=True, options=get_subjects_list())
        level = ft.Dropdown(label=t("level"), expand=True, options=get_study_levels())
        price = ft.TextField(label=t("price"), expand=True, keyboard_type=ft.KeyboardType.NUMBER, value=APP_SETTINGS.get("default_amount", "500"))
        status = ft.Text("", weight=ft.FontWeight.BOLD)

        def save_group(_):
            if not name.value or not subject.value or not level.value:
                status.value, status.color = t("error_fields"), "red"
                page.update()
                return
            try:
                conn = get_db_connection()
                conn.execute("INSERT INTO groups(name,level,price,subject) VALUES(?,?,?,?)",
                             (name.value.strip(), level.value, float(price.value or 0), subject.value))
                conn.commit()
                conn.close()
                name.value, subject.value, level.value = "", None, None
                status.value, status.color = t("saved_success"), "green"
                page.update()
            except Exception as ex:
                status.value, status.color = f"خطأ: {ex}", "red"
                page.update()

        content = ft.Container(
            width=responsive_width(), padding=20,
            content=ft.Column([
                ft.Text(t("groups"), size=22, weight=ft.FontWeight.BOLD, color="#1D4ED8"),
                name, subject, level, price, status,
                create_btn(t("save"), save_group, "green"),
                create_btn("📋 عرض وتعديل المجموعات", open_groups_list, "#F59E0B"),
                create_btn(t("back_home"), go_home, "#475569"),
            ], spacing=10)
        )
        navigate(content)

    def delete_group(gid):
        def do_delete():
            conn = get_db_connection()
            try:
                conn.execute("DELETE FROM groups WHERE id=?", (gid,))
                conn.commit()
                open_groups_list()
            except sqlite3.IntegrityError:
                show_error_snack(
                    "لا يمكن حذف هذه المجموعة لوجود سجلات حضور أو مدفوعات مرتبطة بها."
                )
            finally:
                conn.close()

        confirm_delete("هل أنت متأكد من حذف هذه المجموعة؟ لا يمكن التراجع عن هذا الإجراء.", do_delete)

    def open_groups_list(_=None):
        conn = get_db_connection()
        rows = conn.execute("SELECT id, name, subject, level, price FROM groups ORDER BY name COLLATE NOCASE").fetchall()
        conn.close()

        col = ft.Column(spacing=8)
        for gid, name, subj, lvl, price in rows:
            col.controls.append(
                ft.Container(
                    padding=10, bgcolor="white", border_radius=10,
                    content=ft.Column([
                        ft.Text(f"{name} - {subj or ''} ({lvl or ''})\n{money(price)} {APP_SETTINGS.get('currency','دج')}"),
                        ft.Row([
                            ft.ElevatedButton(t("edit"), bgcolor="#F59E0B", color="white", expand=True, on_click=lambda e, x=gid: edit_group_screen(x)),
                            ft.ElevatedButton(t("delete"), bgcolor="#DC2626", color="white", expand=True, on_click=lambda e, x=gid: delete_group(x)),
                        ]),
                    ], spacing=6)
                )
            )
        
        content = ft.Container(
            width=responsive_width(), padding=20,
            content=ft.Column([
                ft.Text(t("groups"), size=22, weight=ft.FontWeight.BOLD, color="#1D4ED8"),
                col,
                create_btn(t("back"), open_groups_screen, "#475569"),
            ], spacing=10)
        )
        navigate(content)

    def edit_group_screen(group_id):
        conn = get_db_connection()
        g = conn.execute("SELECT name, level, price, subject FROM groups WHERE id=?", (group_id,)).fetchone()
        conn.close()

        name = ft.TextField(label="اسم المجموعة", expand=True, value=g[0] if g else "")
        subject = ft.Dropdown(label=t("subject"), expand=True, value=g[3] if g else None, options=get_subjects_list())
        level = ft.Dropdown(label=t("level"), expand=True, value=g[1] if g else None, options=get_study_levels())
        price = ft.TextField(label=t("price"), expand=True, keyboard_type=ft.KeyboardType.NUMBER, value=str(g[2]) if g else "0")
        status = ft.Text("", weight=ft.FontWeight.BOLD)

        def update_group(_):
            try:
                conn = get_db_connection()
                conn.execute("UPDATE groups SET name=?, level=?, price=?, subject=? WHERE id=?",
                             (name.value.strip(), level.value, float(price.value or 0), subject.value, group_id))
                conn.commit()
                conn.close()
                status.value, status.color = t("saved_success"), "green"
                page.update()
            except Exception as ex:
                status.value, status.color = f"خطأ: {ex}", "red"
                page.update()

        content = ft.Container(
            width=responsive_width(), padding=20,
            content=ft.Column([
                ft.Text(t("edit"), size=22, weight=ft.FontWeight.BOLD, color="#1D4ED8"),
                name, subject, level, price, status,
                create_btn(t("save"), update_group, "green"),
                create_btn(t("back"), open_groups_list, "#475569"),
            ], spacing=10)
        )
        navigate(content)

    # -------------------- التلاميذ --------------------
    def open_students_screen(_=None):
        name = ft.TextField(label=t("name"), expand=True)
        level = ft.Dropdown(label=t("level"), expand=True, options=get_study_levels())
        phone = ft.TextField(label=t("phone"), expand=True, keyboard_type=ft.KeyboardType.PHONE)
        status = ft.Text("", weight=ft.FontWeight.BOLD)

        _, groups_data = group_options()
        group_checkboxes = []
        group_col = ft.Column(spacing=4)
        for g in groups_data:
            cb = ft.Checkbox(label=f"{g[1]} - {g[2] or ''} ({g[3] or ''})", data=g[0])
            group_checkboxes.append(cb)
            group_col.controls.append(cb)

        def save_student(_):
            selected_groups = [cb.data for cb in group_checkboxes if cb.value]
            if not name.value or not level.value:
                status.value, status.color = t("error_fields"), "red"
                page.update()
                return
            try:
                conn = get_db_connection()
                cur = conn.cursor()
                cur.execute("INSERT INTO students(name,level,phone) VALUES(?,?,?)",
                            (name.value.strip(), level.value, phone.value.strip()))
                student_id = cur.lastrowid
                for gid in selected_groups:
                    cur.execute("INSERT INTO student_groups(student_id, group_id) VALUES(?,?)", (student_id, gid))
                conn.commit()
                conn.close()
                name.value, level.value, phone.value = "", None, ""
                for cb in group_checkboxes: cb.value = False
                status.value, status.color = t("saved_success"), "green"
                page.update()
            except Exception as ex:
                status.value, status.color = f"خطأ: {ex}", "red"
                page.update()

        content = ft.Container(
            width=responsive_width(), padding=20,
            content=ft.Column([
                ft.Text(t("students"), size=22, weight=ft.FontWeight.BOLD, color="#1D4ED8"),
                name, level, phone,
                ft.Text(t("select_groups"), weight=ft.FontWeight.BOLD),
                group_col, status,
                create_btn(t("save"), save_student, "green"),
                create_btn("📋 عرض وتعديل التلاميذ", open_students_list, "#F59E0B"),
                create_btn(t("back_home"), go_home, "#475569"),
            ], spacing=10)
        )
        navigate(content)

    def delete_student(sid):
        def do_delete():
            conn = get_db_connection()
            try:
                conn.execute("DELETE FROM students WHERE id=?", (sid,))
                conn.commit()
                open_students_list()
            except sqlite3.IntegrityError:
                show_error_snack(
                    "لا يمكن حذف هذا التلميذ لوجود سجلات حضور أو مدفوعات مرتبطة به."
                )
            finally:
                conn.close()

        confirm_delete("هل أنت متأكد من حذف هذا التلميذ؟ لا يمكن التراجع عن هذا الإجراء.", do_delete)

    def open_students_list(_=None):
        search_tf = ft.TextField(label="🔍 البحث باسم الطالب أو المجموعة", expand=True)
        col = ft.Column(spacing=8)

        def filter_students(e=None):
            col.controls.clear()
            query = (search_tf.value or "").strip().lower()
            conn = get_db_connection()
            students = conn.execute("SELECT id, name, level FROM students ORDER BY name COLLATE NOCASE").fetchall()
            
            for sid, sname, slvl in students:
                grps = conn.execute("""
                    SELECT g.name FROM groups g 
                    JOIN student_groups sg ON sg.group_id=g.id 
                    WHERE sg.student_id=?
                """, (sid,)).fetchall()
                grp_names = [g[0] for g in grps]
                grp_str = ", ".join(grp_names) if grp_names else "بدون مجموعة"
                
                if not query or (query in sname.lower()) or any(query in g.lower() for g in grp_names):
                    col.controls.append(
                        ft.Container(
                            padding=10, bgcolor="white", border_radius=10,
                            content=ft.Column([
                                ft.Text(f"{sname}\n{slvl or ''} — [{grp_str}]"),
                                ft.Row([
                                    ft.ElevatedButton(t("edit"), bgcolor="#F59E0B", color="white", expand=True, on_click=lambda e, x=sid: edit_student_screen(x)),
                                    ft.ElevatedButton(t("delete"), bgcolor="#DC2626", color="white", expand=True, on_click=lambda e, x=sid: delete_student(x)),
                                ]),
                            ], spacing=6)
                        )
                    )
            conn.close()
            page.update()

        search_tf.on_change = filter_students
        filter_students()

        content = ft.Container(
            width=responsive_width(), padding=20,
            content=ft.Column([
                ft.Text(t("students"), size=22, weight=ft.FontWeight.BOLD, color="#1D4ED8"),
                search_tf, col,
                create_btn(t("back"), open_students_screen, "#475569"),
            ], spacing=10)
        )
        navigate(content)

    def edit_student_screen(student_id):
        conn = get_db_connection()
        st = conn.execute("SELECT name, level, phone FROM students WHERE id=?", (student_id,)).fetchone()
        assigned_grps = [r[0] for r in conn.execute("SELECT group_id FROM student_groups WHERE student_id=?", (student_id,)).fetchall()]
        _, groups_data = group_options()
        conn.close()

        name = ft.TextField(label=t("name"), expand=True, value=st[0] if st else "")
        level = ft.Dropdown(label=t("level"), expand=True, value=st[1] if st else None, options=get_study_levels())
        phone = ft.TextField(label=t("phone"), expand=True, value=st[2] if st else "")
        status = ft.Text("", weight=ft.FontWeight.BOLD)

        group_checkboxes = []
        group_col = ft.Column(spacing=4)
        for g in groups_data:
            cb = ft.Checkbox(label=f"{g[1]} - {g[2] or ''} ({g[3] or ''})", data=g[0], value=(g[0] in assigned_grps))
            group_checkboxes.append(cb)
            group_col.controls.append(cb)

        def save_edit(_):
            selected_groups = [cb.data for cb in group_checkboxes if cb.value]
            try:
                conn = get_db_connection()
                cur = conn.cursor()
                cur.execute("UPDATE students SET name=?, level=?, phone=? WHERE id=?",
                            (name.value.strip(), level.value, phone.value.strip(), student_id))
                cur.execute("DELETE FROM student_groups WHERE student_id=?", (student_id,))
                for gid in selected_groups:
                    cur.execute("INSERT INTO student_groups(student_id, group_id) VALUES(?,?)", (student_id, gid))
                conn.commit()
                conn.close()
                status.value, status.color = t("saved_success"), "green"
                page.update()
            except Exception as ex:
                status.value, status.color = f"خطأ: {ex}", "red"
                page.update()

        content = ft.Container(
            width=responsive_width(), padding=20,
            content=ft.Column([
                ft.Text(t("edit"), size=22, weight=ft.FontWeight.BOLD, color="#1D4ED8"),
                name, level, phone,
                ft.Text(t("select_groups"), weight=ft.FontWeight.BOLD),
                group_col, status,
                create_btn(t("save"), save_edit, "green"),
                create_btn(t("back"), open_students_list, "#475569"),
            ], spacing=10)
        )
        navigate(content)

    # -------------------- تسجيل الحضور --------------------
    def open_attendance_screen(_=None):
        opts, _ = group_options()
        group_dd = ft.Dropdown(label="اختر المجموعة", expand=True, options=opts)
        date_field = ft.TextField(
            label=t("date"), expand=True, value=date.today().isoformat()
        )
        list_col = ft.Column(spacing=8)
        records = []
        status = ft.Text("", weight=ft.FontWeight.BOLD)

        def on_date_picked(e):
            if e.control.value:
                date_field.value = e.control.value.strftime("%Y-%m-%d")
                load_students()
                page.update()

        date_picker = ft.DatePicker(
            first_date=datetime(2020, 1, 1),
            last_date=datetime(2035, 12, 31),
            value=datetime.combine(date.today(), datetime.min.time()),
            on_change=on_date_picked,
        )

        def open_calendar(_):
            try:
                page.open(date_picker)
            except AttributeError:
                if date_picker not in page.overlay:
                    page.overlay.append(date_picker)
                date_picker.open = True
                page.update()

        def is_valid_date(value):
            """يتحقق أن التاريخ المُدخل يدويًا بصيغة YYYY-MM-DD صحيحة."""
            try:
                datetime.strptime((value or "").strip(), "%Y-%m-%d")
                return True
            except (ValueError, TypeError):
                return False

        def set_date_today(_):
            date_field.value = date.today().isoformat()
            load_students()

        def set_date_last_record(_):
            """يضبط الحقل على تاريخ آخر تسجيل حضور محفوظ لهذه المجموعة في قاعدة البيانات."""
            if not group_dd.value:
                status.value, status.color = "يرجى اختيار المجموعة أولاً.", "red"
                page.update()
                return
            gid = int(group_dd.value)
            conn = get_db_connection()
            row = conn.execute(
                "SELECT MAX(date) FROM attendance WHERE group_id=?", (gid,)
            ).fetchone()
            conn.close()
            last_date = row[0] if row and row[0] else None
            if last_date:
                date_field.value = last_date[:10]
                load_students()
            else:
                status.value, status.color = "لا يوجد تسجيل سابق لهذه المجموعة.", "orange"
            page.update()

        # ألوان وإشارات الحالة (حالتان فقط الآن: حاضر / غائب — الدفع أصبح عملية منفصلة تمامًا)
        STATUS_COLORS_BG = {
            "present": "#BBF7D0",  # أخضر: حاضر
            "absent": "#FECACA",   # أحمر: غائب
        }
        STATUS_COLORS_BORDER = {
            "present": "#16A34A",
            "absent": "#DC2626",
        }
        STATUS_ICONS = {
            "present": "✅",
            "absent": "❌",
        }

        def status_bg_color(status_value):
            return STATUS_COLORS_BG.get(status_value, "#FFFFFF")

        def status_border_color(status_value):
            return STATUS_COLORS_BORDER.get(status_value, "#94A3B8")

        def status_emoji(status_value):
            return STATUS_ICONS.get(status_value, "")

        def create_student_row(sid, sname, initial_status="present"):
            current_status = {"value": initial_status}

            name_text = ft.Text(f"{status_emoji(initial_status)} {sname}", weight=ft.FontWeight.BOLD)

            row_container = ft.Container(
                bgcolor=status_bg_color(initial_status),
                border=ft.border.Border(left=ft.border.BorderSide(6, status_border_color(initial_status))),
                border_radius=10, padding=10,
            )

            def set_status(new_status, refresh_page=True):
                """يطبّق الحالة الجديدة (حاضر/غائب) على كل عناصر الصف."""
                current_status["value"] = new_status
                row_container.bgcolor = status_bg_color(new_status)
                row_container.border = ft.border.Border(left=ft.border.BorderSide(6, status_border_color(new_status)))
                name_text.value = f"{status_emoji(new_status)} {sname}"
                for key, btn in status_buttons.items():
                    btn.style = ft.ButtonStyle(
                        bgcolor=STATUS_COLORS_BORDER[key] if key == new_status else "#E2E8F0",
                        color="white" if key == new_status else "#334155",
                    )
                if refresh_page:
                    row_container.update()
                    page.update()

            def get_status():
                return current_status["value"]

            status_buttons = {
                "present": ft.ElevatedButton("✅ حاضر", on_click=lambda e: set_status("present"), expand=True),
                "absent": ft.ElevatedButton("❌ غائب", on_click=lambda e: set_status("absent"), expand=True),
            }
            # تلوين أولي للأزرار حسب الحالة الحالية
            for key, btn in status_buttons.items():
                btn.style = ft.ButtonStyle(
                    bgcolor=STATUS_COLORS_BORDER[key] if key == initial_status else "#E2E8F0",
                    color="white" if key == initial_status else "#334155",
                )

            row_container.content = ft.Column([
                name_text,
                ft.Row(
                    [status_buttons["present"], status_buttons["absent"]],
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                ),
            ])

            return row_container, get_status, set_status, name_text


        def load_students(e=None):
            # نبني قائمة عناصر جديدة بالكامل ثم نستبدل بها controls دفعة واحدة
            # (بدل clear() ثم append() تدريجيًا)، لأن الاستبدال الكامل هو ما
            # يضمن اكتشاف Flet للتغيير ويُظهر القائمة فعليًا في كل الحالات —
            # وليس فقط عند التحميل الأولي للشاشة — بما في ذلك تغيير المجموعة
            # من القائمة المنسدلة أو الضغط على زر التحديث 🔄.
            new_controls = []
            new_records = []
            status.value = ""

            if not group_dd.value:
                new_controls.append(ft.Text("اختر مجموعة أولاً.", color="red"))
                list_col.controls = new_controls
                records.clear()
                page.update()
                return

            current_date = (date_field.value or "").strip()
            if not is_valid_date(current_date):
                new_controls.append(
                    ft.Text(
                        "⚠️ صيغة التاريخ غير صحيحة. يجب أن تكون بالشكل YYYY-MM-DD (مثال: 2026-09-11).",
                        color="red", text_align=ft.TextAlign.CENTER
                    )
                )
                list_col.controls = new_controls
                records.clear()
                page.update()
                return

            try:
                gid = int(group_dd.value)
                conn = get_db_connection()

                rows = conn.execute(
                    """
                    SELECT DISTINCT s.id, s.name
                    FROM students s
                    INNER JOIN student_groups sg ON sg.student_id = s.id
                    WHERE sg.group_id = ?
                    ORDER BY s.name COLLATE NOCASE
                    """,
                    (gid,)
                ).fetchall()

                if not rows:
                    new_controls.append(ft.Text("لا يوجد تلاميذ مسجلين في هذه المجموعة.", color="red", text_align=ft.TextAlign.CENTER))
                else:
                    for sid, sname in rows:
                        existing_att = conn.execute(
                            "SELECT status FROM attendance WHERE student_id=? AND group_id=? AND substr(date,1,10)=?",
                            (sid, gid, current_date)
                        ).fetchone()

                        db_status = "absent" if (existing_att and existing_att[0] == STATUS_ABSENT) else "present"

                        row_container, get_status, set_status, name_text = create_student_row(sid, sname, db_status)

                        new_records.append({
                            "student_id": sid,
                            "get_status": get_status,
                            "set_status": set_status,
                            "container": row_container,
                            "name_text": name_text,
                            "sname": sname,
                        })

                        new_controls.append(row_container)
                conn.close()
            except Exception as ex:
                new_controls = [ft.Text(f"خطأ في تحميل التلاميذ: {ex}", color="red")]
                status.value, status.color = f"خطأ في تحميل التلاميذ: {ex}", "red"

            records.clear()
            records.extend(new_records)
            list_col.controls = new_controls
            page.update()

        group_dd.on_change = load_students
        
        refresh_btn = ft.ElevatedButton(
            "🔄", 
            on_click=load_students, 
            width=60, 
            tooltip="تحديث القائمة"
        )

        def open_attendance_history(_=None):
            """يعرض قائمة الأيام المسجَّلة مسبقًا لهذه المجموعة، ويتيح اختيار يوم لتعديله."""
            if not group_dd.value:
                status.value, status.color = "⚠️ اختر مجموعة أولاً لعرض سجل أيامها.", "red"
                page.update()
                return

            gid = int(group_dd.value)
            conn = get_db_connection()
            saved_dates = conn.execute(
                """
                SELECT substr(date,1,10) AS d, COUNT(*) AS cnt,
                       SUM(CASE WHEN status=? THEN 1 ELSE 0 END) AS absent_cnt
                FROM attendance
                WHERE group_id=?
                GROUP BY d
                ORDER BY d DESC
                """,
                (STATUS_ABSENT, gid)
            ).fetchall()
            conn.close()

            if not saved_dates:
                show_error_snack("لا يوجد أي سجل حضور محفوظ مسبقًا لهذه المجموعة.")
                return

            def pick_date(chosen_date):
                def handler(_):
                    close_dialog(dlg)
                    date_field.value = chosen_date
                    load_students()
                return handler

            rows_ui = []
            for d, cnt, absent_cnt in saved_dates:
                rows_ui.append(
                    ft.TextButton(
                        content=ft.Text(f"📅 {d}   —   {cnt} تلميذ ({absent_cnt} غائب)"),
                        on_click=pick_date(d),
                    )
                )

            dlg = ft.AlertDialog(
                modal=True,
                title=ft.Text("📜 سجل الأيام السابقة — اختر يومًا لتعديله"),
                content=ft.Column(rows_ui, scroll=ft.ScrollMode.AUTO, tight=True, height=350, width=responsive_width(380)),
                actions=[ft.TextButton("إغلاق", on_click=lambda e: close_dialog(dlg))],
            )
            show_dialog(dlg)

        history_btn = ft.ElevatedButton(
            "📜 سجل الأيام السابقة", bgcolor="#7C3AED", color="white",
            on_click=open_attendance_history,
        )

        def mark_all(status_value):
            """يعلّم كل التلاميذ بنفس الحالة دفعة واحدة (حاضر / غائب)."""
            def apply(_=None):
                for rec in records:
                    rec["set_status"](status_value, refresh_page=False)
                page.update()
            return apply

        mark_all_present_btn = ft.ElevatedButton(
            "✅ تحديد الكل حاضر", bgcolor="#16A34A", color="white", expand=True,
            on_click=mark_all("present"),
        )
        mark_all_absent_btn = ft.ElevatedButton(
            "❌ تحديد الكل غائب", bgcolor="#DC2626", color="white", expand=True,
            on_click=mark_all("absent"),
        )

        def perform_save(gid, attendance_date):
            conn = None
            try:
                dt = f"{attendance_date} {datetime.now().strftime('%H:%M')}"
                conn = get_db_connection()

                for rec in records:
                    sid = rec["student_id"]
                    status_value = rec["get_status"]()  # "present" | "absent"
                    status_text = STATUS_ABSENT if status_value == "absent" else STATUS_PRESENT

                    # مسح أي سجل حضور سابق لهذا التلميذ في هذا اليوم، ثم إدخال السجل الجديد
                    # (الحضور لا يُنشئ أي دفعة تلقائيًا بعد الآن — الدفع عملية منفصلة تمامًا،
                    # تُسجَّل من شاشة المدفوعات وقتما يدفع التلميذ فعليًا)
                    conn.execute("DELETE FROM attendance WHERE student_id=? AND group_id=? AND substr(date,1,10)=?", (sid, gid, attendance_date))
                    conn.execute(
                        "INSERT INTO attendance(student_id,group_id,date,status,amount) VALUES(?,?,?,?,?)",
                        (sid, gid, dt, status_text, 0)
                    )

                conn.commit()
                status.value, status.color = t("saved_success"), "green"
            except Exception as ex:
                if conn:
                    conn.rollback()
                status.value, status.color = f"خطأ أثناء الحفظ: {ex}", "red"
            finally:
                if conn:
                    conn.close()
                page.update()

        def save_attendance(_):
            if not group_dd.value or not records:
                status.value, status.color = t("error_fields"), "red"
                page.update()
                return

            attendance_date = (date_field.value or "").strip()
            if not is_valid_date(attendance_date):
                status.value, status.color = (
                    "⚠️ صيغة التاريخ غير صحيحة. يجب أن تكون بالشكل YYYY-MM-DD.", "red"
                )
                page.update()
                return

            gid = int(group_dd.value)

            # تحقق: هل سبق تسجيل حضور لهذه المجموعة في نفس اليوم؟ إن كان كذلك، نحذّر قبل الاستبدال
            conn = get_db_connection()
            already_saved = conn.execute(
                "SELECT COUNT(*) FROM attendance WHERE group_id=? AND substr(date,1,10)=?",
                (gid, attendance_date)
            ).fetchone()[0] > 0
            conn.close()

            if already_saved:
                dlg = ft.AlertDialog(
                    modal=True,
                    title=ft.Text("⚠️ تنبيه: تسجيل مكرر"),
                    content=ft.Text(
                        f"تم تسجيل حضور هذه المجموعة بتاريخ {attendance_date} من قبل.\n"
                        "هل تريد استبدال السجل المحفوظ سابقًا بالتعديلات الحالية؟"
                    ),
                    actions=[
                        ft.TextButton("إلغاء", on_click=lambda e: close_dialog(dlg)),
                        ft.TextButton(
                            "✅ نعم، استبدل السجل",
                            style=ft.ButtonStyle(color="#D97706"),
                            on_click=lambda e: (close_dialog(dlg), perform_save(gid, attendance_date)),
                        ),
                    ],
                )
                show_dialog(dlg)
            else:
                perform_save(gid, attendance_date)

        edit_saved_btn = ft.ElevatedButton(
            "✏️ تعديل تسجيل الحضور المحفوظ", bgcolor="#F59E0B", color="white",
            on_click=load_students,
            tooltip="يعيد تحميل آخر نسخة محفوظة لهذا اليوم من قاعدة البيانات، متجاهلاً أي تعديلات لم تُحفظ بعد",
        )

        content = ft.Container(
            width=responsive_width(), padding=20,
            content=ft.Column([
                ft.Text(t("attendance"), size=22, weight=ft.FontWeight.BOLD, color="#1D4ED8"),
                ft.Row([group_dd, refresh_btn]),
                ft.Row([date_field, ft.ElevatedButton("📅", on_click=open_calendar, width=60)]),
                ft.Row([
                    ft.TextButton("اليوم", on_click=set_date_today),
                    ft.TextButton("🕓 آخر تسجيل", on_click=set_date_last_record),
                ]),
                ft.Row([mark_all_present_btn, mark_all_absent_btn]),
                history_btn,
                status,
                list_col,
                create_btn(t("save"), save_attendance, "green"),
                edit_saved_btn,
                create_btn(t("back_home"), go_home, "#475569"),
            ], spacing=10),
        )
        navigate(content)

        if opts:
            group_dd.value = opts[0].key
            load_students()

    # -------------------- المدفوعات --------------------
    def open_payments_screen(_=None):
        today_str = date.today().isoformat()
        month_str = datetime.now().strftime("%Y-%m")

        conn = get_db_connection()

        pay_today = conn.execute(
            "SELECT COALESCE(SUM(amount),0) FROM payments "
            "WHERE substr(payment_date,1,10)=?",
            (today_str,)
        ).fetchone()[0] or 0

        pay_month = conn.execute(
            "SELECT COALESCE(SUM(amount),0) FROM payments "
            "WHERE substr(payment_date,1,7)=?",
            (month_str,)
        ).fetchone()[0] or 0

        pay_total = conn.execute(
            "SELECT COALESCE(SUM(amount),0) FROM payments"
        ).fetchone()[0] or 0

        # الدَّين الجديد = (عدد الحصص المحضورة × سعر الحصة) - المدفوع، لكل (تلميذ، مجموعة)
        debt_map = compute_debt_map(conn)
        total_debts = sum(debt_map.values())

        # تجميع الدَّين حسب التلميذ (قد يكون له دَين في أكثر من مجموعة)
        student_names = dict(conn.execute("SELECT id, name FROM students").fetchall())
        per_student_debt = {}
        per_student_breakdown = {}
        for (sid, gid), debt in debt_map.items():
            if debt > 0.01:
                per_student_debt[sid] = per_student_debt.get(sid, 0) + debt
                per_student_breakdown.setdefault(sid, []).append((gid, debt))

        conn.close()

        def settle_debt(sid, sname, debt_amount, breakdown):
            def do_settle():
                conn2 = get_db_connection()
                try:
                    for gid, amt in breakdown:
                        if amt > 0:
                            conn2.execute(
                                "INSERT INTO payments(student_id,group_id,amount,payment_date,payment_type,notes) "
                                "VALUES(?,?,?,?,?,?)",
                                (sid, gid, amt, date.today().isoformat(), "تسوية دين", "تسوية دَين مستحق")
                            )
                    conn2.commit()
                    open_payments_screen()
                except Exception as ex:
                    conn2.rollback()
                    show_error_snack(f"خطأ أثناء تسوية الدَّين: {ex}")
                finally:
                    conn2.close()

            confirm_delete(
                f"سيتم تسجيل دفعة بقيمة {money(debt_amount)} {APP_SETTINGS.get('currency','دج')} لتسوية دَين {sname} بالكامل. متابعة؟",
                do_settle
            )

        def open_pay_all_dialog(_=None):
            """
            يعرض قائمة كل المدينين مع خانة اختيار لكل واحد (محدَّدة افتراضيًا).
            عند التأكيد، تُسوَّى ديون كل من بقيت خانته محدَّدة فقط، بينما
            يُستثنى (يبقى دَينه كما هو) كل من أزال المستخدم علامة الاختيار
            عنه — أي "من لم يدفع فعليًا".
            """
            if not per_student_debt:
                show_error_snack("لا يوجد أي دَين مستحق حاليًا.")
                return

            checks = {}
            rows_ui = []
            for sid, debt_amount in sorted(per_student_debt.items(), key=lambda x: -x[1]):
                sname = student_names.get(sid, "؟")
                cb = ft.Checkbox(
                    label=f"{sname} — {money(debt_amount)} {APP_SETTINGS.get('currency','دج')}",
                    value=True,
                )
                checks[sid] = cb
                rows_ui.append(cb)

            def do_pay_all():
                excluded = [sid for sid, cb in checks.items() if not cb.value]
                to_settle = [sid for sid, cb in checks.items() if cb.value]
                if not to_settle:
                    show_error_snack("لم يتم اختيار أي تلميذ للتسوية.")
                    return
                conn6 = get_db_connection()
                try:
                    for sid in to_settle:
                        for gid, amt in per_student_breakdown[sid]:
                            if amt > 0:
                                conn6.execute(
                                    "INSERT INTO payments(student_id,group_id,amount,payment_date,payment_type,notes) "
                                    "VALUES(?,?,?,?,?,?)",
                                    (sid, gid, amt, date.today().isoformat(), "تسوية دين", "تسوية جماعية لكل الديون")
                                )
                    conn6.commit()
                    msg = f"✅ تم تسوية ديون {len(to_settle)} تلميذ."
                    if excluded:
                        excluded_names = ", ".join(student_names.get(s, "؟") for s in excluded)
                        msg += f"\n⏭️ تم استثناء (لم يدفعوا): {excluded_names}"
                    show_error_snack(msg)
                    open_payments_screen()
                except Exception as ex:
                    conn6.rollback()
                    show_error_snack(f"خطأ أثناء التسوية الجماعية: {ex}")
                finally:
                    conn6.close()

            def toggle_all(new_value):
                def handler(_):
                    for cb in checks.values():
                        cb.value = new_value
                    dlg.update()
                return handler

            dlg = ft.AlertDialog(
                modal=True,
                title=ft.Text("💰 تسوية ديون الجميع"),
                content=ft.Column(
                    [
                        ft.Text(
                            "كل التلاميذ محدَّدون افتراضيًا. أزل علامة الاختيار عن "
                            "من لم يدفع فعليًا لاستثنائه من التسوية.",
                            size=12, color="#64748B",
                        ),
                        ft.Row([
                            ft.TextButton("✅ تحديد الكل", on_click=toggle_all(True)),
                            ft.TextButton("❌ إلغاء تحديد الكل", on_click=toggle_all(False)),
                        ]),
                        ft.Column(rows_ui, scroll=ft.ScrollMode.AUTO, tight=True, height=300, width=responsive_width(380)),
                    ],
                    tight=True,
                ),
                actions=[
                    ft.TextButton("إلغاء", on_click=lambda e: close_dialog(dlg)),
                    ft.TextButton(
                        "✅ تأكيد التسوية للمحدَّدين",
                        style=ft.ButtonStyle(color="#16A34A"),
                        on_click=lambda e: (close_dialog(dlg), do_pay_all()),
                    ),
                ],
            )
            show_dialog(dlg)

        debtors_col = ft.Column(spacing=8)
        if per_student_debt:
            for sid, debt_amount in sorted(per_student_debt.items(), key=lambda x: -x[1]):
                sname = student_names.get(sid, "؟")
                breakdown = per_student_breakdown[sid]
                debtors_col.controls.append(
                    ft.Container(
                        padding=10, bgcolor="white", border_radius=10,
                        content=ft.Column([
                            ft.Text(f"{sname}\nدَين مستحق: {money(debt_amount)} {APP_SETTINGS.get('currency','دج')}"),
                            ft.ElevatedButton(
                                "✅ تسوية الدَّين", bgcolor="#16A34A", color="white", expand=True,
                                on_click=lambda e, x=sid, n=sname, d=debt_amount, b=breakdown: settle_debt(x, n, d, b),
                            ),
                        ], spacing=6)
                    )
                )
        else:
            debtors_col.controls.append(ft.Text("🎉 لا يوجد أي دَين مستحق حاليًا.", color="#16A34A"))

        # ---- تسجيل دفعة يدوية جديدة (قبل الحضور، أو بعد حصة/حصتين/3/4) ----
        conn3 = get_db_connection()
        all_students = conn3.execute("SELECT id, name FROM students ORDER BY name COLLATE NOCASE").fetchall()
        conn3.close()

        new_pay_student_dd = ft.Dropdown(
            label="التلميذ", expand=True,
            options=[ft.dropdown.Option(key=str(sid), text=sname) for sid, sname in all_students]
        )
        new_pay_group_dd = ft.Dropdown(label="المجموعة", expand=True, options=[])
        new_pay_amount = ft.TextField(label="المبلغ المدفوع", expand=True, keyboard_type=ft.KeyboardType.NUMBER)
        new_pay_status = ft.Text("", weight=ft.FontWeight.BOLD)

        def on_pay_student_change(e):
            new_pay_group_dd.options = []
            new_pay_group_dd.value = None
            if new_pay_student_dd.value:
                conn4 = get_db_connection()
                sid = int(new_pay_student_dd.value)
                grps = conn4.execute(
                    "SELECT g.id, g.name FROM groups g "
                    "JOIN student_groups sg ON sg.group_id=g.id WHERE sg.student_id=?",
                    (sid,)
                ).fetchall()
                conn4.close()
                new_pay_group_dd.options = [ft.dropdown.Option(key=str(gid), text=gname) for gid, gname in grps]
            page.update()

        new_pay_student_dd.on_change = on_pay_student_change

        def register_payment(_):
            if not new_pay_student_dd.value or not new_pay_group_dd.value:
                new_pay_status.value, new_pay_status.color = "اختر التلميذ والمجموعة أولاً.", "red"
                page.update()
                return
            try:
                amount_val = float(str(new_pay_amount.value or "0").replace(",", "."))
            except (ValueError, TypeError):
                amount_val = 0
            if amount_val <= 0:
                new_pay_status.value, new_pay_status.color = "أدخل مبلغًا صحيحًا أكبر من صفر.", "red"
                page.update()
                return

            conn5 = get_db_connection()
            try:
                conn5.execute(
                    "INSERT INTO payments(student_id,group_id,amount,payment_date,payment_type,notes) VALUES(?,?,?,?,?,?)",
                    (int(new_pay_student_dd.value), int(new_pay_group_dd.value), amount_val,
                     date.today().isoformat(), "دفعة يدوية", "")
                )
                conn5.commit()
                new_pay_amount.value = ""
                open_payments_screen()
            except Exception as ex:
                conn5.rollback()
                new_pay_status.value, new_pay_status.color = f"خطأ: {ex}", "red"
                page.update()
            finally:
                conn5.close()

        content = ft.Container(
            width=responsive_width(), padding=20,
            content=ft.Column([
                ft.Text(t("payments"), size=22, weight=ft.FontWeight.BOLD, color="#1D4ED8"),
                ft.Text(
                    f"💡 كل مجموعة تُدفع كباقة {SESSIONS_PER_PACKAGE} حصص. الدَّين = (عدد الحصص المحضورة × سعر الحصة) − المدفوع. "
                    "يمكن الدفع مسبقًا أو بعد حصة أو حصتين أو أكثر — النظام يحسب الباقي تلقائيًا.",
                    color="#92400E", size=12
                ),
                ft.Row([
                    create_stat_card(t("today_payments"), money(pay_today), "💵"),
                    create_stat_card(t("month_payments"), money(pay_month), "📅"),
                ]),
                ft.Row([
                    create_stat_card(t("total_payments"), money(pay_total), "🏛️"),
                    create_stat_card(t("debts"), money(total_debts), "⚠️"),
                ]),
                ft.Divider(),
                ft.Text("➕ تسجيل دفعة جديدة", size=16, weight=ft.FontWeight.BOLD),
                new_pay_student_dd,
                new_pay_group_dd,
                new_pay_amount,
                create_btn("💾 تسجيل الدفعة", register_payment, "green"),
                new_pay_status,
                ft.Divider(),
                ft.Text("⚠️ التلاميذ المدينون", size=16, weight=ft.FontWeight.BOLD),
                create_btn("💰 دفع كل الديون (مع إمكانية الاستثناء)", open_pay_all_dialog, "#0EA5E9"),
                debtors_col,
                ft.Divider(),
                create_btn(t("back_home"), go_home, "#475569"),
            ], spacing=10)
        )
        navigate(content)

    # -------------------- التقارير --------------------
    def open_reports_screen(_=None):
        today_str = date.today().isoformat()
        conn = get_db_connection()
        
        present_today = conn.execute(
            "SELECT COUNT(*) FROM attendance WHERE substr(date,1,10)=? AND status!=?",
            (today_str, STATUS_ABSENT)
        ).fetchone()[0]
        absent_today = conn.execute(
            "SELECT COUNT(*) FROM attendance WHERE substr(date,1,10)=? AND status=?",
            (today_str, STATUS_ABSENT)
        ).fetchone()[0]

        groups = conn.execute("SELECT id, name, subject, level, price FROM groups").fetchall()
        groups_report_col = ft.Column(spacing=8)

        # استعلامات مجمّعة فقط (بدل استعلام لكل مجموعة أو لكل تلميذ)
        st_counts = dict(conn.execute(
            "SELECT group_id, COUNT(*) FROM student_groups GROUP BY group_id"
        ).fetchall())
        group_pays = dict(conn.execute(
            "SELECT group_id, COALESCE(SUM(amount),0) FROM payments GROUP BY group_id"
        ).fetchall())

        # ديون كل مجموعة = مجموع ديون كل تلاميذها بالصيغة الجديدة (حصص محضورة × سعر الحصة - مدفوع)
        debt_map = compute_debt_map(conn)
        group_debts = {}
        for (sid, gid), debt in debt_map.items():
            group_debts[gid] = group_debts.get(gid, 0) + debt

        for gid, gname, gsubj, glvl, gprice in groups:
            st_count = st_counts.get(gid, 0)
            grp_pay = group_pays.get(gid, 0)
            grp_debt = group_debts.get(gid, 0)

            groups_report_col.controls.append(
                ft.Container(
                    bgcolor="white", padding=15, border_radius=8,
                    content=ft.Column([
                        ft.Text(f"📚 {gname} - {gsubj or ''} ({glvl or ''})", weight=ft.FontWeight.BOLD, color="#1D4ED8"),
                        ft.Text(f"👥 عدد التلاميذ: {st_count}"),
                        ft.Text(f"💰 المداخيل المحصلة: {money(grp_pay)} {APP_SETTINGS.get('currency','دج')}", color="#16A34A"),
                        ft.Text(f"⚠️ الديون المتبقية: {money(grp_debt)} {APP_SETTINGS.get('currency','دج')}", color="#DC2626"),
                    ])
                )
            )
        conn.close()

        content = ft.Container(
            width=responsive_width(), padding=20,
            content=ft.Column([
                ft.Text(t("reports"), size=22, weight=ft.FontWeight.BOLD, color="#1D4ED8"),
                ft.Text(t("today_attendance"), size=16, weight=ft.FontWeight.BOLD),
                ft.Row([
                    create_stat_card(t("present_today"), present_today, "✅"),
                    create_stat_card(t("absent_today"), absent_today, "❌"),
                ]),
                ft.Divider(),
                ft.Text(t("all_groups_report"), size=16, weight=ft.FontWeight.BOLD),
                groups_report_col,
                ft.Divider(),
                create_btn("📄 تقرير مفصل عن مبلغ كل تلميذ", open_student_financial_report, "#7C3AED"),
                create_btn(t("back_home"), go_home, "#475569"),
            ], spacing=10)
        )
        navigate(content)

    def open_student_financial_report(_=None):
        """
        تقرير مفصّل يعرض لكل تلميذ: تفصيل كل مجموعة (عدد الحصص المحضورة،
        المبلغ المستحق عليها، ما دفعه فعليًا، والباقي عليه)، إضافة لإجمالي
        عام لكل تلميذ (مستحق / مدفوع / دَين متبقٍ).
        """
        conn = get_db_connection()
        group_prices = dict(conn.execute("SELECT id, price FROM groups").fetchall())
        group_names = dict(conn.execute("SELECT id, name FROM groups").fetchall())
        default_amount = float(APP_SETTINGS.get("default_amount") or 0)

        attended = conn.execute(
            "SELECT student_id, group_id, COUNT(*) FROM attendance WHERE status != ? GROUP BY student_id, group_id",
            (STATUS_ABSENT,)
        ).fetchall()
        paid_map = dict(
            ((sid, gid), amt) for sid, gid, amt in conn.execute(
                "SELECT student_id, group_id, COALESCE(SUM(amount),0) FROM payments GROUP BY student_id, group_id"
            ).fetchall()
        )
        student_names = dict(conn.execute("SELECT id, name FROM students").fetchall())
        conn.close()

        # تجميع حسب التلميذ: لكل مجموعة حضر فيها -> (عدد الحصص، سعر الحصة، المستحق، المدفوع، الدَّين)
        per_student = {}
        for sid, gid, attended_cnt in attended:
            price = group_prices.get(gid)
            price = float(price) if price is not None else default_amount
            price_per_session = price / SESSIONS_PER_PACKAGE if price else 0
            due = round(attended_cnt * price_per_session, 2)
            paid = round(paid_map.get((sid, gid), 0) or 0, 2)
            debt = max(0.0, round(due - paid, 2))
            per_student.setdefault(sid, []).append((gid, attended_cnt, price_per_session, due, paid, debt))

        # تلاميذ دفعوا مسبقًا في مجموعة دون أن يحضروا فيها بعد (دفعة مسبقة) — تظهر مدفوعاتهم أيضًا
        for (sid, gid), paid_amt in paid_map.items():
            already = per_student.get(sid, [])
            if not any(g == gid for g, *_ in already):
                per_student.setdefault(sid, []).append((gid, 0, 0, 0.0, round(paid_amt, 2), 0.0))

        col = ft.Column(spacing=10)
        if not per_student:
            col.controls.append(ft.Text("لا يوجد أي بيانات مالية حتى الآن.", color="#64748B"))
        else:
            for sid in sorted(per_student.keys(), key=lambda s: (student_names.get(s) or "")):
                sname = student_names.get(sid, "؟")
                rows = per_student[sid]
                total_due = sum(r[3] for r in rows)
                total_paid = sum(r[4] for r in rows)
                total_debt = sum(r[5] for r in rows)

                detail_lines = []
                for gid, cnt, pps, due, paid, debt in rows:
                    gname = group_names.get(gid, "؟")
                    detail_lines.append(
                        f"• {gname}: {cnt} حصة × {money(pps)} = {money(due)}   "
                        f"(دُفع {money(paid)} — الباقي {money(debt)}) {APP_SETTINGS.get('currency','دج')}"
                    )

                col.controls.append(
                    ft.Container(
                        bgcolor="white", padding=15, border_radius=8,
                        content=ft.Column([
                            ft.Text(f"👤 {sname}", weight=ft.FontWeight.BOLD, color="#1D4ED8", size=16),
                            ft.Text("\n".join(detail_lines), size=12, color="#334155"),
                            ft.Divider(height=1),
                            ft.Text(
                                f"الإجمالي: مستحق {money(total_due)} — مدفوع {money(total_paid)} — "
                                f"دَين متبقٍ {money(total_debt)} {APP_SETTINGS.get('currency','دج')}",
                                weight=ft.FontWeight.BOLD,
                                color=("#DC2626" if total_debt > 0.01 else "#16A34A"),
                            ),
                        ], spacing=4)
                    )
                )

        content = ft.Container(
            width=responsive_width(), padding=20,
            content=ft.Column([
                ft.Text("📄 تقرير مفصل عن مبلغ كل تلميذ", size=22, weight=ft.FontWeight.BOLD, color="#1D4ED8"),
                col,
                create_btn(t("back"), open_reports_screen, "#475569"),
            ], spacing=10)
        )
        navigate(content)

    # -------------------- الإعدادات --------------------
    def open_settings_screen(_=None):
        teacher = ft.TextField(label=t("teacher"), expand=True, value=APP_SETTINGS.get("teacher_name", ""))
        center = ft.TextField(label="اسم المركز / النشاط", expand=True, value=APP_SETTINGS.get("center_name", ""))
        default_amount = ft.TextField(label="المبلغ الافتراضي", expand=True, keyboard_type=ft.KeyboardType.NUMBER, value=APP_SETTINGS.get("default_amount", "500"))
        currency_field = ft.TextField(label=t("currency"), expand=True, value=APP_SETTINGS.get("currency", "دج"))
        
        # ملاحظة: خيار الفرنسية أُزيل مؤقتًا لأن قاموس TRANSLATIONS
        # لا يحتوي حاليًا على ترجمات فرنسية فعلية (كان اختيارها يُبقي
        # كل النصوص بالعربية دون أي تنبيه للمستخدم). يمكن إعادته
        # لاحقًا بعد إضافة قسم "fr" كامل داخل TRANSLATIONS.
        lang_dd = ft.Dropdown(
            label=t("lang_setting"), expand=True,
            value=APP_SETTINGS.get("lang", "ar"),
            options=[ft.dropdown.Option("ar", "العربية")]
        )
        status = ft.Text("", weight=ft.FontWeight.BOLD)

        def save_settings(_):
            values = {
                "teacher_name": teacher.value.strip(),
                "center_name": center.value.strip(),
                "default_amount": default_amount.value.strip() or "500",
                "currency": currency_field.value.strip() or "دج",
                "theme": "light",
                "lang": lang_dd.value or "ar",
            }
            save_settings_file(values)
            page.rtl = (values["lang"] == "ar")
            page.theme_mode = ft.ThemeMode.LIGHT
            status.value, status.color = t("saved_success"), "green"
            page.update()

        # يحفظ آخر نسخة داخلية أُنشئت، لنعرف أي ملف نُصدّره عند اختيار المستخدم
        # لمكان الحفظ (Downloads مثلاً)، لأن مجلد التطبيق الداخلي على أندرويد
        # غير مرئي لمدير الملفات العادي.
        last_backup_path = {"path": None}

        def backup_database(_):
            try:
                BACKUP_DIR.mkdir(exist_ok=True)
                stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                target = BACKUP_DIR / f"tutor_data_backup_{stamp}.db"
                shutil.copy2(DB_PATH, target)
                last_backup_path["path"] = target
                status.value, status.color = (
                    f"✅ تم إنشاء نسخة داخلية ({target.name}).\n"
                    "اضغط الآن على '📤 حفظ النسخة في مكان آخر' لحفظها في مكان "
                    "يمكنك الوصول إليه لاحقًا (مثل Downloads)، وإلا فستبقى محفوظة "
                    "داخل التطبيق فقط.",
                    "green",
                )
            except Exception as ex:
                status.value, status.color = f"خطأ: {ex}", "red"
            page.update()

        def on_export_result(e: ft.FilePickerResultEvent):
            if not e.path:
                return  # المستخدم ألغى الاختيار
            try:
                shutil.copy2(last_backup_path["path"], e.path)
                status.value, status.color = "✅ تم حفظ النسخة الاحتياطية في المكان الذي اخترته.", "green"
            except Exception as ex:
                status.value, status.color = f"خطأ أثناء الحفظ: {ex}", "red"
            page.update()

        def export_backup(_):
            if not last_backup_path["path"]:
                status.value, status.color = "أنشئ نسخة احتياطية أولًا بالضغط على الزر الذي يليه.", "red"
                page.update()
                return
            backup_file_picker.on_result = on_export_result
            backup_file_picker.save_file(
                file_name=last_backup_path["path"].name,
                allowed_extensions=["db"],
            )

        def on_restore_result(e: ft.FilePickerResultEvent):
            if not e.files:
                return  # المستخدم ألغى الاختيار
            picked = e.files[0]
            if not picked.path:
                status.value, status.color = "تعذّر الوصول لمسار الملف المختار على هذا الجهاز.", "red"
                page.update()
                return

            def do_restore():
                try:
                    # نأخذ نسخة أمان من القاعدة الحالية قبل الاستبدال، احتياطًا
                    BACKUP_DIR.mkdir(exist_ok=True)
                    safety_stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                    shutil.copy2(DB_PATH, BACKUP_DIR / f"before_restore_{safety_stamp}.db")
                    shutil.copy2(picked.path, DB_PATH)
                    status.value, status.color = (
                        "✅ تم استرجاع النسخة الاحتياطية بنجاح. "
                        "أعد فتح الشاشات لرؤية البيانات المستوردة.",
                        "green",
                    )
                except Exception as ex:
                    status.value, status.color = f"خطأ أثناء الاسترجاع: {ex}", "red"
                page.update()

            confirm_delete(
                f"سيتم استبدال كل البيانات الحالية بمحتوى الملف '{picked.name}'. "
                "سيُحفظ نسخة أمان من البيانات الحالية تلقائيًا قبل الاستبدال. متابعة؟",
                do_restore,
            )

        def import_backup(_):
            backup_file_picker.on_result = on_restore_result
            backup_file_picker.pick_files(allow_multiple=False, allowed_extensions=["db"])

        content = ft.Container(
            width=responsive_width(), padding=20,
            content=ft.Column([
                ft.Text(t("settings"), size=22, weight=ft.FontWeight.BOLD, color="#1D4ED8"),
                teacher, center, default_amount, currency_field, lang_dd, status,
                create_btn(t("save"), save_settings, "green"),
                ft.Divider(),
                ft.Text("📦 النسخ الاحتياطي والاستعادة", size=16, weight=ft.FontWeight.BOLD),
                create_btn(t("backup"), backup_database, "#7C3AED"),
                create_btn("📤 حفظ النسخة في مكان آخر", export_backup, "#0EA5E9"),
                create_btn("📥 استيراد نسخة احتياطية", import_backup, "#EA580C"),
                create_btn(t("back_home"), go_home, "#475569"),
            ], spacing=10)
        )
        navigate(content)

    # -------------------- لوحة التحكم --------------------
    def show_main_view(_=None):
        conn = get_db_connection()
        students_count = conn.execute("SELECT COUNT(*) FROM students").fetchone()[0]
        groups_count = conn.execute("SELECT COUNT(*) FROM groups").fetchone()[0]
        conn.close()

        header_text = APP_SETTINGS.get("center_name") or t("app_title")
        if APP_SETTINGS.get("teacher_name"):
            header_text += f"\n{t('teacher')}: {APP_SETTINGS['teacher_name']}"

        content = ft.Container(
            width=responsive_width(), padding=20,
            content=ft.Column([
                ft.Text(header_text, size=22, weight=ft.FontWeight.BOLD, color="#1E3A8A", text_align=ft.TextAlign.CENTER),
                ft.Row([
                    create_stat_card(t("student_count"), students_count, "👨‍🎓"),
                    create_stat_card(t("group_count"), groups_count, "📚"),
                ]),
                ft.Divider(),
                create_btn(f"👨‍🎓 {t('students')}", open_students_screen),
                create_btn(f"📚 {t('groups')}", open_groups_screen),
                create_btn(f"✅ {t('attendance')}", open_attendance_screen),
                create_btn(f"💰 {t('payments')}", open_payments_screen, "#16A34A"),
                create_btn(f"📊 {t('reports')}", open_reports_screen, "#7C3AED"),
                create_btn(f"⚙️ {t('settings')}", open_settings_screen, "#475569"),
                create_btn("🔴 إغلاق البرنامج", close_app, "#991B1B"),
            ], spacing=12, horizontal_alignment=ft.CrossAxisAlignment.CENTER)
        )
        navigate(content, is_home=True)

    try:
        show_main_view()
    except Exception as ex:
        # لو حدث أي خطأ غير متوقع أثناء بدء التطبيق (بدل شاشة بيضاء تختفي فجأة)
        # نعرض رسالة الخطأ نصيًا مباشرة على الشاشة، لتسهيل تشخيص السبب.
        import traceback
        traceback.print_exc()
        error_details = traceback.format_exc()
        main_view.controls = [
            ft.Row(
                [
                    ft.Container(
                        width=responsive_width(),
                        padding=20,
                        content=ft.Column(
                            [
                                ft.Text("⚠️ حدث خطأ أثناء بدء التطبيق", size=18, weight=ft.FontWeight.BOLD, color="red"),
                                ft.Text(str(ex), color="red", selectable=True),
                                ft.Divider(),
                                ft.Text(error_details, size=10, color="#64748B", selectable=True),
                            ],
                            spacing=10,
                            scroll=ft.ScrollMode.AUTO,
                        ),
                    )
                ],
                alignment=ft.MainAxisAlignment.CENTER,
            )
        ]
        page.update()

if __name__ == "__main__":
    if hasattr(ft, "run"):
        ft.run(target=main)
    else:
        ft.app(target=main)
