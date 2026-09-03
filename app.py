"""
ValveBox Hub — سرور مرکزی برای مدیریت چند تا دستگاه ValveBox از یک سایت.

هر ValveBox هر چند ثانیه یک‌بار وضعیت زنده‌ی خودش (مقادیر گاز، آلارم‌ها)
رو به این سرور می‌فرسته (POST /api/push/<device_id>، با یک توکن
اختصاصی). این سرور اون داده‌ها رو توی حافظه نگه می‌داره و به کاربرهای
لاگین‌شده، بر اساس دسترسی‌شون، نشون می‌ده.

این سرور جدا از خود ValveBoxهاست و باید جایی اجرا بشه که همیشه در
دسترس باشه (یک VPS ساده، یا یکی از همون رزبری پای‌ها اگه فقط قراره
داخل شبکه‌ی بیمارستان استفاده بشه).

فایل‌های تنظیمات (کنار همین app.py):
  - devices.json   لیست دستگاه‌ها: {device_id: {name, zone, hospital, public_url, token}}
  - hub_users.json  کاربرهای این سایت مرکزی و دسترسی‌شون به دستگاه‌ها
  - hospitals.json  لیست بیمارستان‌ها: {hospital_id: {name}}
  - hub_history.db  (خودکار ساخته می‌شود) تاریخچه‌ی آلارم‌های هر دستگاه —
    هر بار که یک گاز از حالت "ok" به یک حالت آلارم (یا برعکس) می‌رود، یک
    رکورد اینجا ثبت می‌شود. این حافظه‌ی خودِ هاب است (جدا از هر دیتابیس
    دیگری که ممکن است روی خود دستگاه یا اسکریپت‌های گزارش‌گیری وجود داشته
    باشد).

نصب پیش‌نیاز:
    pip install fastapi uvicorn python-dotenv itsdangerous
اجرا:
    uvicorn app:app --host 0.0.0.0 --port 9000
"""

import hashlib
import json
import os
import secrets
import shutil
import smtplib
import sqlite3
import time
import zipfile
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from pathlib import Path

import requests
from dotenv import load_dotenv
from fastapi import FastAPI, Form, Request, Header
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from starlette.middleware.sessions import SessionMiddleware

APP_DIR = Path(__file__).resolve().parent
load_dotenv(APP_DIR / ".env")

DEVICES_FILE = APP_DIR / "devices.json"
USERS_FILE = APP_DIR / "hub_users.json"
HOSPITALS_FILE = APP_DIR / "hospitals.json"
HISTORY_DB_FILE = APP_DIR / "hub_history.db"

ONLINE_TIMEOUT_SECONDS = int(os.environ.get("VALVEBOX_HUB_ONLINE_TIMEOUT", "30"))
GASES = ["O2", "N2O", "AIR", "CO2", "VAC"]

# ایمیل بازیابی رمز — از یک حساب Gmail و یک App Password استفاده می‌کند
# (نه رمز خود اکانت). مراحل ساخت App Password در README توضیح داده شده.
GMAIL_ADDRESS = os.environ.get("VALVEBOX_GMAIL_ADDRESS", "")
GMAIL_APP_PASSWORD = os.environ.get("VALVEBOX_GMAIL_APP_PASSWORD", "")
PUBLIC_BASE_URL = os.environ.get("VALVEBOX_HUB_PUBLIC_URL", "https://valvebox.ir")
RESET_TOKEN_TTL_MINUTES = 30

# محافظت در برابر حدس زدن رمز عبور (brute-force)
MAX_LOGIN_ATTEMPTS = 5
LOGIN_LOCKOUT_MINUTES = 15

# ورود به پنل مدیریت به تأیید دوباره‌ی رمز عبور نیاز دارد (حتی اگر کاربر
# از قبل وارد شده باشد)، و این تأیید فقط برای مدت محدودی معتبر می‌ماند.
ADMIN_REVERIFY_MINUTES = 30

# پشتیبان‌گیری خودکار
BACKUP_DIR = APP_DIR / "backups"
BACKUP_KEEP_DAYS = 30

# اعلان فوری (Firebase Cloud Messaging) — اختیاری؛ اگر تنظیم نشده باشد،
# سیستم بدون خطا کار می‌کند، فقط پوش فوری نمی‌فرستد.
FIREBASE_PROJECT_ID = os.environ.get("VALVEBOX_FIREBASE_PROJECT_ID", "")
FIREBASE_SERVICE_ACCOUNT_FILE = os.environ.get("VALVEBOX_FIREBASE_SERVICE_ACCOUNT_FILE", "")

# گزارش هوشمند (اختیاری) — اگر کلید Anthropic تنظیم نشده باشد، گزارش فقط
# به‌صورت آمار خام (بدون هزینه) نمایش داده می‌شود.
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

app = FastAPI()

SESSION_SECRET = os.environ.get("VALVEBOX_HUB_SESSION_SECRET")
if not SESSION_SECRET:
    SESSION_SECRET = os.urandom(32).hex()
    print("[هشدار] VALVEBOX_HUB_SESSION_SECRET در .env تنظیم نشده؛ یک کلید موقت ساخته شد.")
COOKIE_SECURE = os.environ.get("VALVEBOX_HUB_COOKIE_SECURE", "true").lower() != "false"
app.add_middleware(
    SessionMiddleware,
    secret_key=SESSION_SECRET,
    https_only=COOKIE_SECURE,
    same_site="lax",
)

# {device_id: {"payload": {...}, "last_seen": datetime}}
LIVE_CACHE = {}


# ---------------------------------------------------------------------------
# دیتابیس تاریخچه‌ی آلارم‌ها (مخصوص خود هاب)
# ---------------------------------------------------------------------------
def init_history_db():
    conn = sqlite3.connect(HISTORY_DB_FILE)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS alarm_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            device_id TEXT NOT NULL,
            gas_type TEXT NOT NULL,
            alarm_state TEXT NOT NULL,
            start_time TEXT NOT NULL,
            end_time TEXT,
            resolved_by TEXT,
            duration_sec INTEGER
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS password_reset_tokens (
            token TEXT PRIMARY KEY,
            username TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            used INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT,
            action TEXT NOT NULL,
            detail TEXT,
            timestamp TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS push_tokens (
            username TEXT NOT NULL,
            token TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (username, token)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS gas_readings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            device_id TEXT NOT NULL,
            gas_type TEXT NOT NULL,
            value REAL,
            unit TEXT,
            timestamp TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_gas_readings_lookup ON gas_readings (device_id, gas_type, timestamp)"
    )
    conn.commit()
    conn.close()


init_history_db()


def _gauge_alarm(payload, gas):
    if not payload:
        return "ok"
    gauges = payload.get("gauges") or {}
    g = gauges.get(gas) or {}
    return (g.get("alarm") or "ok").lower()


def record_alarm_transitions(device_id, old_payload, new_payload):
    """
    وضعیت قبلی و جدید هر گاز را مقایسه می‌کند: اگر گازی تازه وارد حالت
    آلارم شده، یک رکورد باز جدید ثبت می‌کند؛ اگر گازی از آلارم به حالت
    عادی برگشته، آخرین رکورد بازِ آن گاز را می‌بندد (end_time و duration
    را پر می‌کند).

    خروجی: لیستی از (gas, alarm_state) برای آلارم‌هایی که همین الان تازه
    باز شده‌اند — برای فرستادن اعلان فوری استفاده می‌شود.
    """
    now = datetime.now()
    now_str = now.strftime("%Y-%m-%d %H:%M:%S")
    conn = sqlite3.connect(HISTORY_DB_FILE)
    newly_opened = []

    for gas in GASES:
        old_alarm = _gauge_alarm(old_payload, gas)
        new_alarm = _gauge_alarm(new_payload, gas)

        if old_alarm == new_alarm:
            continue

        if new_alarm != "ok":
            # ورود تازه به حالت آلارم -> رکورد باز جدید
            conn.execute(
                """
                INSERT INTO alarm_events (device_id, gas_type, alarm_state, start_time)
                VALUES (?, ?, ?, ?)
                """,
                (device_id, gas, new_alarm, now_str),
            )
            newly_opened.append((gas, new_alarm))
        elif old_alarm != "ok" and new_alarm == "ok":
            # برگشت به حالت عادی -> بستن آخرین رکورد بازِ همین گاز
            row = conn.execute(
                """
                SELECT id, start_time FROM alarm_events
                WHERE device_id = ? AND gas_type = ? AND end_time IS NULL
                ORDER BY id DESC LIMIT 1
                """,
                (device_id, gas),
            ).fetchone()
            if row:
                event_id, start_time_str = row
                try:
                    start_dt = datetime.strptime(start_time_str, "%Y-%m-%d %H:%M:%S")
                    duration = int((now - start_dt).total_seconds())
                except Exception:
                    duration = None
                conn.execute(
                    """
                    UPDATE alarm_events
                    SET end_time = ?, duration_sec = ?
                    WHERE id = ?
                    """,
                    (now_str, duration, event_id),
                )

    conn.commit()
    conn.close()
    return newly_opened


# ---------------------------------------------------------------------------
# ثبت مقدار دقیق هر گاز در هر پوش — پایه‌ی نمودار روند (Trends). این جدا از
# alarm_events است: اینجا مقدار واقعی (نه فقط وضعیت آلارم) ذخیره می‌شود تا
# نمودار روند بر پایه‌ی داده‌ی خام و قابل استناد باشد.
# ---------------------------------------------------------------------------
GAS_READINGS_RETENTION_DAYS = 30


def record_gas_readings(device_id, payload):
    if not payload:
        return
    gauges = payload.get("gauges") or {}
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    conn = sqlite3.connect(HISTORY_DB_FILE)
    for gas in GASES:
        g = gauges.get(gas)
        if not g:
            continue
        value = g.get("value")
        unit = g.get("unit", "")
        if value is None:
            continue
        conn.execute(
            "INSERT INTO gas_readings (device_id, gas_type, value, unit, timestamp) VALUES (?, ?, ?, ?, ?)",
            (device_id, gas, float(value), unit, now_str),
        )
    conn.commit()
    conn.close()


def prune_old_gas_readings():
    cutoff = (datetime.now() - timedelta(days=GAS_READINGS_RETENTION_DAYS)).strftime("%Y-%m-%d %H:%M:%S")
    conn = sqlite3.connect(HISTORY_DB_FILE)
    conn.execute("DELETE FROM gas_readings WHERE timestamp < ?", (cutoff,))
    conn.commit()
    conn.close()


def fetch_gas_trend(device_id, gas, start, end, limit=5000):
    """مقادیر خام و دقیق ثبت‌شده را برمی‌گرداند — بدون هیچ تخمین یا میان‌یابی،
    دقیقاً همان چیزی که خود دستگاه فرستاده، برای این‌که نمودار قابل استناد
    باشد (هر نقطه = یک خواندش واقعی، با زمان دقیقش)."""
    conn = sqlite3.connect(HISTORY_DB_FILE)
    rows = conn.execute(
        """
        SELECT timestamp, value, unit FROM gas_readings
        WHERE device_id = ? AND gas_type = ? AND timestamp >= ? AND timestamp <= ?
        ORDER BY timestamp ASC
        LIMIT ?
        """,
        (device_id, gas, start, end, limit),
    ).fetchall()
    conn.close()
    return [{"timestamp": r[0], "value": r[1], "unit": r[2]} for r in rows]


def fetch_device_history(device_id, limit=100):
    conn = sqlite3.connect(HISTORY_DB_FILE)
    rows = conn.execute(
        """
        SELECT gas_type, alarm_state, start_time, end_time, resolved_by, duration_sec
        FROM alarm_events
        WHERE device_id = ?
        ORDER BY id DESC
        LIMIT ?
        """,
        (device_id, limit),
    ).fetchall()
    conn.close()
    return [
        {
            "gas_type": r[0],
            "alarm_state": r[1],
            "start_time": r[2],
            "end_time": r[3],
            "resolved_by": r[4],
            "duration_sec": r[5],
        }
        for r in rows
    ]


# ---------------------------------------------------------------------------
# ۶) لاگ فعالیت کاربران (Audit Log)
# ---------------------------------------------------------------------------
def log_audit(username, action, detail=""):
    conn = sqlite3.connect(HISTORY_DB_FILE)
    conn.execute(
        "INSERT INTO audit_log (username, action, detail, timestamp) VALUES (?, ?, ?, ?)",
        (username, action, detail, datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
    )
    conn.commit()
    conn.close()


def fetch_audit_log(limit=200):
    conn = sqlite3.connect(HISTORY_DB_FILE)
    rows = conn.execute(
        "SELECT username, action, detail, timestamp FROM audit_log ORDER BY id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    conn.close()
    return [{"username": r[0], "action": r[1], "detail": r[2], "timestamp": r[3]} for r in rows]


# ---------------------------------------------------------------------------
# ۲) محافظت در برابر حدس زدن رمز عبور (brute-force)
# ---------------------------------------------------------------------------
# {key: {"failures": int, "locked_until": timestamp|None}}   — فقط در حافظه؛
# با ری‌استارت سرور پاک می‌شود، که برای این مورد مشکلی ندارد.
_LOGIN_ATTEMPTS = {}


def _attempt_key(request: Request, username: str) -> str:
    ip = request.client.host if request.client else "unknown"
    return f"{ip}:{username.strip().lower()}"


def is_login_locked(request: Request, username: str) -> bool:
    key = _attempt_key(request, username)
    info = _LOGIN_ATTEMPTS.get(key)
    if not info:
        return False
    locked_until = info.get("locked_until")
    if locked_until and time.time() < locked_until:
        return True
    if locked_until and time.time() >= locked_until:
        _LOGIN_ATTEMPTS.pop(key, None)
    return False


def register_login_failure(request: Request, username: str):
    key = _attempt_key(request, username)
    info = _LOGIN_ATTEMPTS.setdefault(key, {"failures": 0, "locked_until": None})
    info["failures"] += 1
    if info["failures"] >= MAX_LOGIN_ATTEMPTS:
        info["locked_until"] = time.time() + LOGIN_LOCKOUT_MINUTES * 60


def reset_login_failures(request: Request, username: str):
    key = _attempt_key(request, username)
    _LOGIN_ATTEMPTS.pop(key, None)


# ---------------------------------------------------------------------------
# ۴) پشتیبان‌گیری خودکار
# ---------------------------------------------------------------------------
def run_backup():
    BACKUP_DIR.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = BACKUP_DIR / f"backup_{stamp}.zip"

    files_to_backup = [DEVICES_FILE, USERS_FILE, HOSPITALS_FILE, HISTORY_DB_FILE]
    with zipfile.ZipFile(backup_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in files_to_backup:
            if f.exists():
                zf.write(f, arcname=f.name)

    # پاک‌سازی بک‌آپ‌های قدیمی‌تر از BACKUP_KEEP_DAYS روز
    cutoff = time.time() - BACKUP_KEEP_DAYS * 86400
    for old in BACKUP_DIR.glob("backup_*.zip"):
        if old.stat().st_mtime < cutoff:
            old.unlink(missing_ok=True)

    return backup_path


def list_backups():
    if not BACKUP_DIR.exists():
        return []
    files = sorted(BACKUP_DIR.glob("backup_*.zip"), reverse=True)
    return [{"name": f.name, "size_kb": round(f.stat().st_size / 1024, 1)} for f in files]


# ---------------------------------------------------------------------------
# ۱) اعلان فوری با Firebase Cloud Messaging (اختیاری)
# ---------------------------------------------------------------------------
def register_push_token(username: str, token: str):
    conn = sqlite3.connect(HISTORY_DB_FILE)
    conn.execute(
        """
        INSERT INTO push_tokens (username, token, updated_at) VALUES (?, ?, ?)
        ON CONFLICT(username, token) DO UPDATE SET updated_at = excluded.updated_at
        """,
        (username, token, datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
    )
    conn.commit()
    conn.close()


def _firebase_access_token():
    """با فایل سرویس‌اکانت فایربیس، یک access token موقت برای FCM v1 API می‌سازد."""
    from google.oauth2 import service_account
    from google.auth.transport.requests import Request as GoogleRequest

    creds = service_account.Credentials.from_service_account_file(
        FIREBASE_SERVICE_ACCOUNT_FILE,
        scopes=["https://www.googleapis.com/auth/firebase.messaging"],
    )
    creds.refresh(GoogleRequest())
    return creds.token


def send_push_to_tokens(tokens, title: str, body: str):
    if not FIREBASE_PROJECT_ID or not FIREBASE_SERVICE_ACCOUNT_FILE:
        return  # فایربیس تنظیم نشده — بی‌صدا رد شو (سیستم بدون پوش فوری هم کار می‌کند)
    if not tokens:
        return

    try:
        access_token = _firebase_access_token()
    except Exception as e:
        print(f"[خطا] گرفتن توکن فایربیس ناموفق بود: {e}")
        return

    url = f"https://fcm.googleapis.com/v1/projects/{FIREBASE_PROJECT_ID}/messages:send"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json; UTF-8",
    }
    for token in tokens:
        message = {
            "message": {
                "token": token,
                "notification": {"title": title, "body": body},
                "android": {"priority": "high"},
            }
        }
        try:
            requests.post(url, headers=headers, json=message, timeout=10)
        except Exception as e:
            print(f"[خطا] ارسال پوش به یک توکن ناموفق بود: {e}")


def notify_hospital_users(hospital_id: str, title: str, body: str):
    """به همه‌ی کاربران یک بیمارستان که توکن پوش ثبت کرده‌اند، اعلان می‌فرستد."""
    users = load_users()
    usernames = [uname for uname, info in users.items() if info.get("hospital") == hospital_id]
    if not usernames:
        return

    conn = sqlite3.connect(HISTORY_DB_FILE)
    placeholders = ",".join("?" for _ in usernames)
    rows = conn.execute(
        f"SELECT DISTINCT token FROM push_tokens WHERE username IN ({placeholders})",
        usernames,
    ).fetchall()
    conn.close()
    tokens = [r[0] for r in rows]
    send_push_to_tokens(tokens, title, body)


# ---------------------------------------------------------------------------
# ۵) گزارش هوشمند از تاریخچه (آمار خام همیشه رایگان؛ متن روایی فقط اگر
# کلید Anthropic تنظیم شده باشد)
# ---------------------------------------------------------------------------
def build_report_stats(device_id: str, start: str, end: str):
    conn = sqlite3.connect(HISTORY_DB_FILE)
    rows = conn.execute(
        """
        SELECT gas_type, alarm_state, start_time, end_time, duration_sec
        FROM alarm_events
        WHERE device_id = ? AND start_time >= ? AND start_time < ?
        ORDER BY start_time ASC
        """,
        (device_id, start, end),
    ).fetchall()
    conn.close()

    per_gas = {}
    durations = []
    for gas, alarm_state, start_time, end_time, duration_sec in rows:
        per_gas.setdefault(gas, {"count": 0})
        per_gas[gas]["count"] += 1
        if duration_sec is not None:
            durations.append(duration_sec)

    return {
        "device_id": device_id,
        "period_start": start,
        "period_end": end,
        "total_events": len(rows),
        "per_gas": per_gas,
        "avg_duration_sec": (sum(durations) / len(durations)) if durations else None,
        "max_duration_sec": max(durations) if durations else None,
        "raw_events": [
            {"gas_type": r[0], "alarm_state": r[1], "start_time": r[2], "end_time": r[3], "duration_sec": r[4]}
            for r in rows
        ],
    }


def build_ai_narrative(stats: dict) -> str:
    if not ANTHROPIC_API_KEY:
        return ""
    prompt = f"""داده‌ی زیر آمار آلارم‌های یک دستگاه ولوباکس در یک بازه‌ی زمانی است.
این را به یک گزارش کوتاه و روان فارسی برای مدیر بیمارستان تبدیل کن. فقط بر
اساس همین اعداد بنویس، چیزی اضافه نکن.

داده (JSON):
{json.dumps(stats, ensure_ascii=False, indent=2)}
"""
    try:
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": "claude-haiku-4-5",
                "max_tokens": 700,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        parts = [b["text"] for b in data.get("content", []) if b.get("type") == "text"]
        return "\n".join(parts).strip()
    except Exception as e:
        return f"(تولید گزارش هوشمند ناموفق بود: {e})"


# ---------------------------------------------------------------------------
# بازیابی رمز عبور با ایمیل
# ---------------------------------------------------------------------------
def create_reset_token(username: str) -> str:
    token = secrets.token_urlsafe(32)
    expires_at = (datetime.now() + timedelta(minutes=RESET_TOKEN_TTL_MINUTES)).strftime("%Y-%m-%d %H:%M:%S")
    conn = sqlite3.connect(HISTORY_DB_FILE)
    conn.execute(
        "INSERT INTO password_reset_tokens (token, username, expires_at, used) VALUES (?, ?, ?, 0)",
        (token, username, expires_at),
    )
    conn.commit()
    conn.close()
    return token


def validate_reset_token(token: str):
    """اگر توکن معتبر (پیدا شده، استفاده‌نشده، منقضی‌نشده) باشد، نام کاربری را برمی‌گرداند؛ وگرنه None."""
    conn = sqlite3.connect(HISTORY_DB_FILE)
    row = conn.execute(
        "SELECT username, expires_at, used FROM password_reset_tokens WHERE token = ?",
        (token,),
    ).fetchone()
    conn.close()
    if not row:
        return None
    username, expires_at, used = row
    if used:
        return None
    try:
        expires_dt = datetime.strptime(expires_at, "%Y-%m-%d %H:%M:%S")
    except Exception:
        return None
    if datetime.now() > expires_dt:
        return None
    return username


def mark_token_used(token: str):
    conn = sqlite3.connect(HISTORY_DB_FILE)
    conn.execute("UPDATE password_reset_tokens SET used = 1 WHERE token = ?", (token,))
    conn.commit()
    conn.close()


def send_reset_email(to_email: str, reset_link: str) -> bool:
    if not GMAIL_ADDRESS or not GMAIL_APP_PASSWORD:
        print("[هشدار] VALVEBOX_GMAIL_ADDRESS یا VALVEBOX_GMAIL_APP_PASSWORD در .env تنظیم نشده — ایمیل ارسال نشد.")
        return False

    body = f"""برای تنظیم رمز عبور جدید حساب ValveBox خود، روی لینک زیر بزنید
(این لینک تا {RESET_TOKEN_TTL_MINUTES} دقیقه معتبر است):

{reset_link}

اگر خودتان این درخواست را نداده‌اید، این ایمیل را نادیده بگیرید.
"""
    msg = MIMEText(body, _charset="utf-8")
    msg["Subject"] = "بازیابی رمز عبور ValveBox"
    msg["From"] = GMAIL_ADDRESS
    msg["To"] = to_email

    try:
        with smtplib.SMTP("smtp.gmail.com", 587) as server:
            server.starttls()
            server.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
            server.sendmail(GMAIL_ADDRESS, [to_email], msg.as_string())
        return True
    except Exception as e:
        print(f"[خطا] ارسال ایمیل بازیابی رمز ناموفق بود: {e}")
        return False


# ---------------------------------------------------------------------------
# فایل‌های تنظیمات
# ---------------------------------------------------------------------------
def load_devices():
    if not DEVICES_FILE.exists():
        return {}
    with open(DEVICES_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def load_users():
    if not USERS_FILE.exists():
        return {}
    with open(USERS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def load_hospitals():
    if not HOSPITALS_FILE.exists():
        return {}
    with open(HOSPITALS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_users(users):
    with open(USERS_FILE, "w", encoding="utf-8") as f:
        json.dump(users, f, ensure_ascii=False, indent=2)


def hash_pw(pw: str) -> str:
    return hashlib.sha256(pw.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# احراز هویت
# ---------------------------------------------------------------------------
def current_user(request: Request):
    return request.session.get("user")


def require_login(request: Request):
    user = current_user(request)
    if not user:
        return None, JSONResponse({"error": "Unauthorized"}, status_code=401)
    return user, None


def require_admin(request: Request):
    user = current_user(request)
    if not user or int(user.get("level", 0)) < 3:
        return None, HTMLResponse("<h2>دسترسی غیرمجاز — این بخش فقط برای مدیر است.</h2>", status_code=403)

    verified_at = request.session.get("admin_verified_at")
    if not verified_at or (time.time() - verified_at) > ADMIN_REVERIFY_MINUTES * 60:
        next_path = request.url.path
        if request.url.query:
            next_path += "?" + request.url.query
        return None, RedirectResponse(url=f"/admin/verify?next={next_path}", status_code=303)

    return user, None


def user_can_see(user, device_id: str, all_devices: dict) -> bool:
    dev = all_devices.get(device_id)
    if not dev:
        return False
    # هر کاربر فقط دستگاه‌های بیمارستان خودش رو می‌بینه، حتی اگه devices="ALL" باشه
    if dev.get("hospital") != user.get("hospital"):
        return False
    devices = user.get("devices", [])
    return devices == "ALL" or device_id in devices


# ---------------------------------------------------------------------------
# دریافت داده‌ی زنده از هر ValveBox
# ---------------------------------------------------------------------------
@app.post("/api/push/{device_id}")
async def push_data(device_id: str, request: Request, authorization: str = Header(None)):
    devices = load_devices()
    dev = devices.get(device_id)
    if not dev:
        return JSONResponse({"error": "Unknown device"}, status_code=404)

    expected = f"Bearer {dev.get('token', '')}"
    if not authorization or authorization != expected:
        return JSONResponse({"error": "Invalid token"}, status_code=403)

    payload = await request.json()

    old_cached = LIVE_CACHE.get(device_id)
    old_payload = old_cached["payload"] if old_cached else None
    newly_opened = record_alarm_transitions(device_id, old_payload, payload)
    record_gas_readings(device_id, payload)

    LIVE_CACHE[device_id] = {"payload": payload, "last_seen": datetime.now()}

    if newly_opened:
        gases_text = "، ".join(f"{gas} ({state.upper()})" for gas, state in newly_opened)
        dev_name = dev.get("name", device_id)
        notify_hospital_users(
            dev.get("hospital", ""),
            title=f"هشدار ولوباکس — {dev_name}",
            body=f"وضعیت آلارم: {gases_text}",
        )

    return {"ok": True}


def device_status(device_id: str):
    cached = LIVE_CACHE.get(device_id)
    if not cached:
        return "offline", None
    age = (datetime.now() - cached["last_seen"]).total_seconds()
    if age > ONLINE_TIMEOUT_SECONDS:
        return "offline", cached["payload"]
    return "online", cached["payload"]


# ---------------------------------------------------------------------------
# صفحه‌ی ورود
# ---------------------------------------------------------------------------
def login_html(error="", hospitals=None, selected_hospital=""):
    hospitals = hospitals or {}
    err_html = f'<div class="err">{error}</div>' if error else ""
    options_html = ""
    for hid, info in hospitals.items():
        options_html += f'<option value="{info.get("name", hid)}">'
    return f"""
    <html lang="fa" dir="rtl">
    <head>
        <meta charset="utf-8">
        <title>ValveBox Hub - Login</title>
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            body {{
                margin:0; background:#0b1220; color:#e2e8f0;
                font-family: Tahoma, Arial, sans-serif;
                display:flex; align-items:center; justify-content:center; height:100vh;
            }}
            .box {{
                background:#111827; border:1px solid #1f2937; border-radius:14px;
                padding:28px; width:100%; max-width:360px;
            }}
            h1 {{ font-size:20px; margin-top:0; }}
            input, select {{
                width:100%; padding:10px 12px; margin-bottom:12px;
                border-radius:8px; border:1px solid #334155; background:#0b1220; color:#e2e8f0;
                font-family: Tahoma, Arial, sans-serif; font-size:14px;
            }}
            button {{
                width:100%; padding:10px; border:none; border-radius:8px;
                background:#2563eb; color:#fff; font-weight:700; cursor:pointer;
            }}
            .err {{ color:#f87171; margin-bottom:10px; font-size:13px; }}
        </style>
    </head>
    <body>
        <form class="box" method="post" action="/login">
            <h1>ValveBox Hub</h1>
            {err_html}
            <input name="hospital" list="hospitals-list" placeholder="Hospital name" value="{selected_hospital}" autocomplete="off" autofocus>
            <datalist id="hospitals-list">
                {options_html}
            </datalist>
            <input name="username" placeholder="Username">
            <input name="password" type="password" placeholder="Password">
            <button type="submit">Login</button>
            <div style="text-align:center;margin-top:12px;">
                <a href="/forgot-password" style="color:#93c5fd;font-size:13px;text-decoration:none;">رمز عبور را فراموش کرده‌اید؟</a>
            </div>
        </form>
    </body>
    </html>
    """


@app.get("/", response_class=HTMLResponse)
async def login_page():
    return HTMLResponse(login_html(hospitals=load_hospitals()))


@app.post("/login")
async def do_login(
    request: Request,
    hospital: str = Form(...),
    username: str = Form(...),
    password: str = Form(...),
):
    if is_login_locked(request, username):
        return HTMLResponse(
            login_html(
                f"به‌دلیل تلاش‌های ناموفق مکرر، تا {LOGIN_LOCKOUT_MINUTES} دقیقه دیگر امکان ورود نیست.",
                load_hospitals(),
                hospital,
            )
        )

    hospitals = load_hospitals()
    users = load_users()
    info = users.get(username)

    typed = (hospital or "").strip().casefold()
    hospital_key = None
    for hid, hinfo in hospitals.items():
        if hinfo.get("name", "").strip().casefold() == typed:
            hospital_key = hid
            break

    valid = (
        hospital_key
        and info
        and info.get("password") == hash_pw(password)
        and info.get("hospital") == hospital_key
    )
    if not valid:
        register_login_failure(request, username)
        log_audit(username, "login_failed", f"hospital={hospital}")
        return HTMLResponse(
            login_html("نام بیمارستان، نام کاربری یا رمز عبور نادرست است.", hospitals, hospital)
        )

    reset_login_failures(request, username)
    log_audit(username, "login_success")

    request.session["user"] = {
        "username": username,
        "name": info.get("name", username),
        "hospital": hospital_key,
        "hospital_name": hospitals.get(hospital_key, {}).get("name", hospital_key),
        "devices": info.get("devices", []),
        "level": info.get("level", 1),
    }
    return RedirectResponse(url="/devices", status_code=303)


@app.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/", status_code=303)


# ---------------------------------------------------------------------------
# بازیابی رمز عبور
# ---------------------------------------------------------------------------
def message_page(title: str, message: str) -> str:
    return f"""
    <html lang="fa" dir="rtl">
    <head>
        <meta charset="utf-8">
        <title>{title}</title>
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            body {{
                margin:0; background:#0b1220; color:#e2e8f0;
                font-family: Tahoma, Arial, sans-serif;
                display:flex; align-items:center; justify-content:center; height:100vh;
            }}
            .box {{
                background:#111827; border:1px solid #1f2937; border-radius:14px;
                padding:28px; width:100%; max-width:380px; text-align:center;
            }}
            a {{ color:#93c5fd; }}
        </style>
    </head>
    <body>
        <div class="box">
            <p>{message}</p>
            <a href="/">بازگشت به صفحه‌ی ورود</a>
        </div>
    </body>
    </html>
    """


def forgot_password_html(error: str = "") -> str:
    err_html = f'<div class="err">{error}</div>' if error else ""
    return f"""
    <html lang="fa" dir="rtl">
    <head>
        <meta charset="utf-8">
        <title>بازیابی رمز عبور — ValveBox</title>
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            body {{
                margin:0; background:#0b1220; color:#e2e8f0;
                font-family: Tahoma, Arial, sans-serif;
                display:flex; align-items:center; justify-content:center; height:100vh;
            }}
            .box {{
                background:#111827; border:1px solid #1f2937; border-radius:14px;
                padding:28px; width:100%; max-width:360px;
            }}
            h1 {{ font-size:18px; margin-top:0; }}
            p.hint {{ font-size:13px; color:#94a3b8; }}
            input {{
                width:100%; padding:10px 12px; margin-bottom:12px;
                border-radius:8px; border:1px solid #334155; background:#0b1220; color:#e2e8f0;
                font-family: Tahoma, Arial, sans-serif; font-size:14px;
            }}
            button {{
                width:100%; padding:10px; border:none; border-radius:8px;
                background:#2563eb; color:#fff; font-weight:700; cursor:pointer;
            }}
            .err {{ color:#f87171; margin-bottom:10px; font-size:13px; }}
            a {{ color:#93c5fd; font-size:13px; }}
        </style>
    </head>
    <body>
        <form class="box" method="post" action="/forgot-password">
            <h1>بازیابی رمز عبور</h1>
            <p class="hint">نام کاربری‌ات را وارد کن — اگر ایمیلی برایش ثبت شده باشد، لینک بازیابی برایش ارسال می‌شود.</p>
            {err_html}
            <input name="username" placeholder="Username" autofocus>
            <button type="submit">ارسال لینک بازیابی</button>
            <div style="text-align:center;margin-top:12px;"><a href="/">بازگشت به ورود</a></div>
        </form>
    </body>
    </html>
    """


@app.get("/forgot-password", response_class=HTMLResponse)
async def forgot_password_page():
    return HTMLResponse(forgot_password_html())


@app.post("/forgot-password")
async def forgot_password_submit(username: str = Form(...)):
    users = load_users()
    info = users.get(username.strip())

    # همیشه یک پیام یکسان نشان می‌دهیم (چه یوزرنیم پیدا شود چه نه)، تا کسی
    # نتواند با امتحان کردن یوزرنیم‌های مختلف بفهمد کدام یوزرنیم واقعاً وجود دارد.
    generic_message = (
        "اگر این نام کاربری در سیستم ثبت باشد و برایش ایمیل تنظیم شده باشد، "
        "یک لینک بازیابی رمز به ایمیلش ارسال شد. صندوق ایمیل (و پوشه‌ی اسپم) را چک کن."
    )

    if info and info.get("email"):
        token = create_reset_token(username.strip())
        reset_link = f"{PUBLIC_BASE_URL}/reset-password?token={token}"
        send_reset_email(info["email"], reset_link)

    return HTMLResponse(message_page("درخواست ثبت شد", generic_message))


# نسخه‌ی JSON همان درخواست بالا — برای اپ اندروید، تا کاربر مجبور نباشد از
# داخل اپ به مرورگر برود؛ درخواست را مستقیم از داخل اپ می‌فرستد.
@app.post("/api/forgot-password")
async def api_forgot_password(request: Request):
    body = await request.json()
    username = (body.get("username") or "").strip()

    users = load_users()
    info = users.get(username)

    generic_message = (
        "اگر این نام کاربری در سیستم ثبت باشد و برایش ایمیل تنظیم شده باشد، "
        "یک لینک بازیابی رمز به ایمیلش ارسال شد. صندوق ایمیل (و پوشه‌ی اسپم) را چک کن."
    )

    if info and info.get("email"):
        token = create_reset_token(username)
        reset_link = f"{PUBLIC_BASE_URL}/reset-password?token={token}"
        send_reset_email(info["email"], reset_link)

    return {"ok": True, "message": generic_message}


def reset_password_html(token: str, error: str = "") -> str:
    err_html = f'<div class="err">{error}</div>' if error else ""
    return f"""
    <html lang="fa" dir="rtl">
    <head>
        <meta charset="utf-8">
        <title>تعیین رمز جدید — ValveBox</title>
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            body {{
                margin:0; background:#0b1220; color:#e2e8f0;
                font-family: Tahoma, Arial, sans-serif;
                display:flex; align-items:center; justify-content:center; height:100vh;
            }}
            .box {{
                background:#111827; border:1px solid #1f2937; border-radius:14px;
                padding:28px; width:100%; max-width:360px;
            }}
            h1 {{ font-size:18px; margin-top:0; }}
            input {{
                width:100%; padding:10px 12px; margin-bottom:12px;
                border-radius:8px; border:1px solid #334155; background:#0b1220; color:#e2e8f0;
                font-family: Tahoma, Arial, sans-serif; font-size:14px;
            }}
            button {{
                width:100%; padding:10px; border:none; border-radius:8px;
                background:#2563eb; color:#fff; font-weight:700; cursor:pointer;
            }}
            .err {{ color:#f87171; margin-bottom:10px; font-size:13px; }}
        </style>
    </head>
    <body>
        <form class="box" method="post" action="/reset-password">
            <h1>تعیین رمز عبور جدید</h1>
            {err_html}
            <input type="hidden" name="token" value="{token}">
            <input name="password" type="password" placeholder="رمز عبور جدید" autofocus>
            <input name="password_confirm" type="password" placeholder="تکرار رمز عبور جدید">
            <button type="submit">ثبت رمز جدید</button>
        </form>
    </body>
    </html>
    """


@app.get("/reset-password", response_class=HTMLResponse)
async def reset_password_page(token: str):
    username = validate_reset_token(token)
    if not username:
        return HTMLResponse(
            message_page("لینک نامعتبر", "این لینک بازیابی نامعتبر یا منقضی‌شده است. دوباره درخواست بده.")
        )
    return HTMLResponse(reset_password_html(token))


@app.post("/reset-password")
async def reset_password_submit(
    token: str = Form(...),
    password: str = Form(...),
    password_confirm: str = Form(...),
):
    username = validate_reset_token(token)
    if not username:
        return HTMLResponse(
            message_page("لینک نامعتبر", "این لینک بازیابی نامعتبر یا منقضی‌شده است. دوباره درخواست بده.")
        )

    if len(password) < 6:
        return HTMLResponse(reset_password_html(token, "رمز عبور باید حداقل ۶ کاراکتر باشد."))
    if password != password_confirm:
        return HTMLResponse(reset_password_html(token, "دو رمز واردشده یکسان نیستند."))

    users = load_users()
    if username not in users:
        return HTMLResponse(message_page("خطا", "این کاربر دیگر وجود ندارد."))

    users[username]["password"] = hash_pw(password)
    save_users(users)
    mark_token_used(token)

    return HTMLResponse(message_page("انجام شد", "رمز عبور با موفقیت تغییر کرد. حالا می‌توانی وارد شوی."))


# ---------------------------------------------------------------------------
# صفحه‌ی انتخاب دستگاه
# ---------------------------------------------------------------------------
def devices_html(user, devices_list):
    cards = ""
    for dev_id, name, zone, status, public_url, ip in devices_list:
        dot_color = "#16a34a" if status == "online" else "#6b7280"
        settings_url = f"{public_url}/settings"
        cards += f"""
        <a class="card" href="{settings_url}">
            <div class="dot" style="background:{dot_color};"></div>
            <div class="card-name">{name}</div>
            <div style="font-size:12px;color:#94a3b8;margin-top:4px;">{zone}</div>
            <div style="font-size:12px;color:#64748b;font-family:monospace;margin-top:2px;">{ip}</div>
            <div class="card-status">{status.upper()}</div>
        </a>
        """

    return f"""
    <html lang="fa" dir="rtl">
    <head>
        <meta charset="utf-8">
        <title>ValveBox Hub - Devices</title>
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            * {{ box-sizing:border-box; }}
            body {{ margin:0; background:#0b1220; color:#e2e8f0; font-family:Tahoma, Arial, sans-serif; }}
            .topbar {{
                display:flex; justify-content:space-between; align-items:center;
                padding:12px 18px; border-bottom:1px solid #1f2937; background:#111827;
            }}
            .grid {{
                display:grid; grid-template-columns:repeat(auto-fill, minmax(160px, 1fr));
                gap:14px; padding:16px; max-width:900px; margin:0 auto;
            }}
            .card {{
                display:block; text-decoration:none; color:inherit;
                background:#111827; border:1px solid #1f2937; border-radius:14px;
                padding:16px; text-align:center;
            }}
            .card:hover {{ border-color:#2563eb; }}
            .dot {{ width:12px; height:12px; border-radius:50%; margin:0 auto 8px; }}
            .card-name {{ font-weight:800; font-size:15px; }}
            .card-status {{ font-size:11px; color:#94a3b8; margin-top:4px; }}
            button {{ border:none; border-radius:8px; padding:8px 12px; font-weight:700; background:#dc2626; color:#fff; }}
        </style>
    </head>
    <body>
        <div class="topbar">
            <strong>ValveBox Hub — {user.get('hospital_name', '')} — {user.get('name')}</strong>
            <div>
                {'<a href="/admin" style="color:#93c5fd;margin-left:14px;text-decoration:none;font-size:13px;">پنل مدیریت</a>' if int(user.get('level', 0)) >= 3 else ''}
                <a href="/logout"><button>Logout</button></a>
            </div>
        </div>
        <div class="grid">
            {cards if cards else '<p style="padding:20px;color:#94a3b8;">No devices assigned to this account.</p>'}
        </div>
    </body>
    </html>
    """


@app.get("/devices", response_class=HTMLResponse)
async def devices_page(request: Request):
    user = current_user(request)
    if not user:
        return RedirectResponse(url="/", status_code=303)

    all_devices = load_devices()
    listing = []
    for dev_id, info in all_devices.items():
        if not user_can_see(user, dev_id, all_devices):
            continue
        status, payload = device_status(dev_id)
        ip = (payload or {}).get("ip", "—")
        listing.append((
            dev_id,
            info.get("name", dev_id),
            info.get("zone", ""),
            status,
            info.get("public_url", "#"),
            ip,
        ))

    return HTMLResponse(devices_html(user, listing))


# ---------------------------------------------------------------------------
# صفحه‌ی نمایش زنده‌ی یک دستگاه خاص
# ---------------------------------------------------------------------------
def device_view_html(user, dev_id, dev_name, public_url):
    return f"""
    <html lang="fa" dir="rtl">
    <head>
        <meta charset="utf-8">
        <title>ValveBox — {dev_name}</title>
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            * {{ box-sizing:border-box; }}
            body {{ margin:0; background:#0b1220; color:#e2e8f0; font-family:Tahoma, Arial, sans-serif; }}
            .topbar {{
                display:flex; justify-content:space-between; align-items:center; gap:10px;
                padding:12px 16px; border-bottom:1px solid #1f2937; background:#111827;
                position:sticky; top:0;
            }}
            .topbar a button {{ border:none; border-radius:8px; padding:8px 12px; font-weight:700; background:#334155; color:#fff; }}
            #status-line {{ text-align:center; font-size:12px; color:#94a3b8; padding:6px 0 2px; }}
            .grid {{
                display:grid; grid-template-columns:repeat(auto-fit, minmax(150px, 1fr));
                gap:14px; padding:14px; max-width:600px; margin:0 auto;
            }}
            .card {{ border-radius:16px; padding:18px 10px; text-align:center; border:3px solid #334155; background:#111827; }}
            .card.ok {{ border-color:#16a34a; }}
            .card.low {{ border-color:#f59e0b; background:#3a2a0e; }}
            .card.high {{ border-color:#ef4444; background:#3a1414; }}
            .card.fault {{ border-color:#a855f7; background:#2a1533; animation:blink 1s infinite; }}
            @keyframes blink {{ 50% {{ opacity:0.45; }} }}
            .gas-name {{ font-size:15px; font-weight:800; color:#cbd5e1; }}
            .gas-value {{ font-size:30px; font-weight:900; margin-top:6px; }}
            .gas-unit {{ font-size:12px; color:#94a3b8; }}
            .gas-state {{ font-size:11px; margin-top:6px; font-weight:700; }}
            .offline-banner {{
                text-align:center; padding:10px; background:#3a1414; color:#fca5a5;
                font-weight:700; display:none;
            }}
        </style>
    </head>
    <body>
        <div class="topbar">
            <a href="/devices"><button>&larr; All Devices</button></a>
            <strong>{dev_name}</strong>
            <a href="/trends/{dev_id}"><button style="background:#334155;">روند گاز</button></a>
            <a href="{public_url}/settings" target="_blank"><button style="background:#16a34a;">Full Control</button></a>
        </div>
        <div id="offline-banner" class="offline-banner">This device is OFFLINE — showing last known values</div>
        <div id="status-line">Connecting…</div>
        <div class="grid" id="grid"></div>

        <script>
            const GASES = ["O2", "N2O", "AIR", "CO2", "VAC"];
            const DEVICE_ID = "{dev_id}";

            function cardHtml(gas) {{
                return `
                    <div class="card ok" id="card-${{gas}}">
                        <div class="gas-name">${{gas}}</div>
                        <div class="gas-value" id="val-${{gas}}">--</div>
                        <div class="gas-unit" id="unit-${{gas}}"></div>
                        <div class="gas-state" id="state-${{gas}}">OK</div>
                    </div>
                `;
            }}
            document.getElementById('grid').innerHTML = GASES.map(cardHtml).join('');

            async function poll() {{
                try {{
                    const res = await fetch('/api/device/' + DEVICE_ID + '/data');
                    if (res.status === 401) {{ window.location.href = '/'; return; }}
                    const data = await res.json();

                    document.getElementById('offline-banner').style.display =
                        data.status === 'offline' ? 'block' : 'none';
                    document.getElementById('status-line').textContent =
                        'Status: ' + data.status + ' — last seen: ' + (data.last_seen || '—');

                    const gauges = (data.payload && data.payload.gauges) || {{}};
                    for (const gas of GASES) {{
                        const g = gauges[gas] || {{}};
                        const alarm = (g.alarm || 'ok').toLowerCase();

                        document.getElementById('val-' + gas).textContent =
                            (alarm === 'fault') ? 'FAULT' : Number(g.value || 0).toFixed(2);
                        document.getElementById('unit-' + gas).textContent = g.unit || '';
                        document.getElementById('state-' + gas).textContent = alarm.toUpperCase();

                        const card = document.getElementById('card-' + gas);
                        card.className = 'card ' + (
                            alarm === 'fault' ? 'fault' :
                            alarm === 'low' ? 'low' :
                            alarm === 'high' ? 'high' : 'ok'
                        );
                    }}
                }} catch (e) {{
                    document.getElementById('status-line').textContent = 'Connection to hub lost — retrying…';
                }}
            }}

            poll();
            setInterval(poll, 3000);
        </script>
    </body>
    </html>
    """


@app.get("/device/{device_id}", response_class=HTMLResponse)
async def device_page(request: Request, device_id: str):
    user = current_user(request)
    if not user:
        return RedirectResponse(url="/", status_code=303)

    all_devices = load_devices()
    if not user_can_see(user, device_id, all_devices):
        return HTMLResponse("<h2>Access denied for this device.</h2>", status_code=403)

    dev = all_devices.get(device_id)
    if not dev:
        return HTMLResponse("<h2>Unknown device.</h2>", status_code=404)

    return HTMLResponse(device_view_html(user, device_id, dev.get("name", device_id), dev.get("public_url", "#")))


@app.get("/api/device/{device_id}/data")
async def device_data_api(request: Request, device_id: str):
    user, err = require_login(request)
    if err:
        return err
    all_devices = load_devices()
    if not user_can_see(user, device_id, all_devices):
        return JSONResponse({"error": "Forbidden"}, status_code=403)

    status, payload = device_status(device_id)
    cached = LIVE_CACHE.get(device_id)
    last_seen = cached["last_seen"].strftime("%Y-%m-%d %H:%M:%S") if cached else None

    return {"status": status, "payload": payload, "last_seen": last_seen}


# ---------------------------------------------------------------------------
# نمودار روند گازها (Trends) — قابل استناد: هر نقطه دقیقاً یک خواندش واقعی
# ثبت‌شده در دیتابیس است، بدون هیچ تخمین یا میان‌یابی. جدول داده‌ی خام هم
# همیشه زیر نمودار نشان داده می‌شود تا هر نقطه قابل بررسی و استناد باشد.
# ---------------------------------------------------------------------------
def trends_html(dev_id: str, dev_name: str) -> str:
    gas_options = "".join(f'<option value="{g}">{g}</option>' for g in GASES)
    return f"""
    <html lang="fa" dir="rtl">
    <head>
        <meta charset="utf-8">
        <title>روند گازها — {dev_name}</title>
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
        <style>
            * {{ box-sizing:border-box; }}
            body {{ margin:0; background:#0b1220; color:#e2e8f0; font-family:Tahoma, Arial, sans-serif; }}
            .topbar {{
                display:flex; justify-content:space-between; align-items:center; gap:10px;
                padding:12px 16px; border-bottom:1px solid #1f2937; background:#111827;
            }}
            .topbar a {{ color:#93c5fd; text-decoration:none; font-size:13px; }}
            .container {{ max-width:900px; margin:0 auto; padding:16px; }}
            .card {{ background:#111827; border:1px solid #1f2937; border-radius:14px; padding:16px; margin-bottom:18px; }}
            select, input {{
                padding:8px 10px; margin-left:8px; margin-bottom:8px;
                border-radius:8px; border:1px solid #334155; background:#0b1220; color:#e2e8f0;
                font-family: Tahoma, Arial, sans-serif; font-size:13px;
            }}
            button {{
                padding:8px 16px; border:none; border-radius:8px;
                background:#2563eb; color:#fff; font-weight:700; cursor:pointer; font-size:13px;
            }}
            table {{ width:100%; border-collapse:collapse; font-size:12px; }}
            th, td {{ text-align:right; padding:6px 8px; border-bottom:1px solid #1f2937; }}
            th {{ color:#94a3b8; position:sticky; top:0; background:#111827; }}
            #tableWrap {{ max-height:400px; overflow-y:auto; }}
            #status {{ font-size:12px; color:#94a3b8; }}
        </style>
    </head>
    <body>
        <div class="topbar">
            <strong>روند گاز — {dev_name}</strong>
            <a href="/device/{dev_id}">بازگشت به دستگاه</a>
        </div>
        <div class="container">
            <div class="card">
                <select id="gasSelect">{gas_options}</select>
                <input type="datetime-local" id="startInput">
                <input type="datetime-local" id="endInput">
                <button onclick="loadTrend()">نمایش نمودار</button>
                <div id="status"></div>
            </div>
            <div class="card">
                <canvas id="trendChart" height="120"></canvas>
            </div>
            <div class="card">
                <h3 style="margin-top:0;">داده‌ی خام (برای استناد)</h3>
                <div id="tableWrap">
                    <table id="dataTable">
                        <thead><tr><th>زمان</th><th>مقدار</th><th>واحد</th></tr></thead>
                        <tbody id="dataBody"></tbody>
                    </table>
                </div>
            </div>
        </div>

        <script>
            const DEVICE_ID = "{dev_id}";
            let chart = null;

            // پیش‌فرض: ۲۴ ساعت اخیر
            const now = new Date();
            const dayAgo = new Date(now.getTime() - 24*60*60*1000);
            function fmt(d) {{ return d.toISOString().slice(0,16); }}
            document.getElementById('startInput').value = fmt(dayAgo);
            document.getElementById('endInput').value = fmt(now);

            async function loadTrend() {{
                const gas = document.getElementById('gasSelect').value;
                const start = document.getElementById('startInput').value.replace('T', ' ');
                const end = document.getElementById('endInput').value.replace('T', ' ');
                document.getElementById('status').textContent = 'در حال دریافت...';

                try {{
                    const res = await fetch(`/api/device/${{DEVICE_ID}}/trends?gas=${{gas}}&start=${{encodeURIComponent(start)}}&end=${{encodeURIComponent(end)}}`);
                    const data = await res.json();
                    const points = data.readings || [];

                    document.getElementById('status').textContent =
                        points.length + ' نقطه‌ی داده‌ی واقعی ثبت‌شده بین ' + start + ' تا ' + end;

                    const labels = points.map(p => p.timestamp);
                    const values = points.map(p => p.value);
                    const unit = points.length ? points[0].unit : '';

                    if (chart) chart.destroy();
                    chart = new Chart(document.getElementById('trendChart'), {{
                        type: 'line',
                        data: {{
                            labels: labels,
                            datasets: [{{
                                label: gas + (unit ? ' (' + unit + ')' : ''),
                                data: values,
                                borderColor: '#2563eb',
                                backgroundColor: 'rgba(37,99,235,0.15)',
                                pointRadius: 2,
                                tension: 0
                            }}]
                        }},
                        options: {{
                            responsive: true,
                            scales: {{
                                x: {{ ticks: {{ color: '#94a3b8', maxTicksLimit: 10 }} }},
                                y: {{ ticks: {{ color: '#94a3b8' }} }}
                            }},
                            plugins: {{ legend: {{ labels: {{ color: '#e2e8f0' }} }} }}
                        }}
                    }});

                    const tbody = document.getElementById('dataBody');
                    tbody.innerHTML = points.map(p =>
                        `<tr><td>${{p.timestamp}}</td><td>${{p.value}}</td><td>${{p.unit||''}}</td></tr>`
                    ).join('');
                }} catch (e) {{
                    document.getElementById('status').textContent = 'خطا در دریافت داده — ممکن است نمودار Chart.js بارگذاری نشده باشد (اتصال اینترنت را چک کن)؛ جدول داده‌ی خام باز هم قابل‌استفاده است.';
                }}
            }}

            loadTrend();
        </script>
    </body>
    </html>
    """


@app.get("/trends/{device_id}", response_class=HTMLResponse)
async def trends_page(request: Request, device_id: str):
    user = current_user(request)
    if not user:
        return RedirectResponse(url="/", status_code=303)
    all_devices = load_devices()
    if not user_can_see(user, device_id, all_devices):
        return HTMLResponse("<h2>دسترسی غیرمجاز.</h2>", status_code=403)
    dev = all_devices.get(device_id)
    if not dev:
        return HTMLResponse("<h2>دستگاه پیدا نشد.</h2>", status_code=404)
    return HTMLResponse(trends_html(device_id, dev.get("name", device_id)))


@app.get("/api/device/{device_id}/trends")
async def device_trends_api(request: Request, device_id: str, gas: str, start: str, end: str):
    user, err = require_login(request)
    if err:
        return err
    all_devices = load_devices()
    if not user_can_see(user, device_id, all_devices):
        return JSONResponse({"error": "Forbidden"}, status_code=403)
    if gas not in GASES:
        return JSONResponse({"error": "گاز نامعتبر است."}, status_code=400)

    readings = fetch_gas_trend(device_id, gas, start, end)
    return {"device_id": device_id, "gas": gas, "readings": readings}


# ---------------------------------------------------------------------------
# API JSON برای اپ اندروید — لیست دستگاه‌های قابل‌مشاهده‌ی کاربر لاگین‌شده
# ---------------------------------------------------------------------------
@app.get("/api/devices")
async def devices_api(request: Request):
    user, err = require_login(request)
    if err:
        return err

    all_devices = load_devices()
    result = []
    for dev_id, info in all_devices.items():
        if not user_can_see(user, dev_id, all_devices):
            continue
        status, payload = device_status(dev_id)
        ip = (payload or {}).get("ip", "")
        result.append({
            "device_id": dev_id,
            "name": info.get("name", dev_id),
            "zone": info.get("zone", ""),
            "status": status,
            "public_url": info.get("public_url", ""),
            "ip": ip,
        })
    return {"devices": result}


# ---------------------------------------------------------------------------
# API JSON برای لاگین (اپ اندروید فرم را به‌صورت JSON می‌فرستد؛ همان بررسی
# اعتبار /login وب، فقط با ورودی/خروجی JSON به‌جای HTML)
# ---------------------------------------------------------------------------
@app.post("/api/login")
async def api_login(request: Request):
    body = await request.json()
    hospital = (body.get("hospital") or "").strip()
    username = (body.get("username") or "").strip()
    password = body.get("password") or ""

    if is_login_locked(request, username):
        return JSONResponse(
            {"error": f"به‌دلیل تلاش‌های ناموفق مکرر، تا {LOGIN_LOCKOUT_MINUTES} دقیقه دیگر امکان ورود نیست."},
            status_code=429,
        )

    hospitals = load_hospitals()
    users = load_users()
    info = users.get(username)

    typed = hospital.casefold()
    hospital_key = None
    for hid, hinfo in hospitals.items():
        if hinfo.get("name", "").strip().casefold() == typed:
            hospital_key = hid
            break

    valid = (
        hospital_key
        and info
        and info.get("password") == hash_pw(password)
        and info.get("hospital") == hospital_key
    )
    if not valid:
        register_login_failure(request, username)
        log_audit(username, "login_failed", f"hospital={hospital} (app)")
        return JSONResponse({"error": "نام بیمارستان، نام کاربری یا رمز عبور نادرست است."}, status_code=401)

    reset_login_failures(request, username)
    log_audit(username, "login_success", "app")

    request.session["user"] = {
        "username": username,
        "name": info.get("name", username),
        "hospital": hospital_key,
        "hospital_name": hospitals.get(hospital_key, {}).get("name", hospital_key),
        "devices": info.get("devices", []),
        "level": info.get("level", 1),
    }
    return {
        "ok": True,
        "name": info.get("name", username),
        "hospital_name": hospitals.get(hospital_key, {}).get("name", hospital_key),
        "level": info.get("level", 1),
    }


# ---------------------------------------------------------------------------
# API JSON برای تاریخچه‌ی آلارم‌های یک دستگاه (برای اپ اندروید)
# ---------------------------------------------------------------------------
@app.get("/api/device/{device_id}/history")
async def device_history_api(request: Request, device_id: str):
    user, err = require_login(request)
    if err:
        return err
    all_devices = load_devices()
    if not user_can_see(user, device_id, all_devices):
        return JSONResponse({"error": "Forbidden"}, status_code=403)

    events = fetch_device_history(device_id)
    return {"events": events}


# ---------------------------------------------------------------------------
# ثبت توکن پوش (اپ اندروید) — برای اعلان فوری
# ---------------------------------------------------------------------------
@app.post("/api/register-push-token")
async def api_register_push_token(request: Request):
    user, err = require_login(request)
    if err:
        return err
    body = await request.json()
    token = (body.get("token") or "").strip()
    if not token:
        return JSONResponse({"error": "توکن ارسال نشد."}, status_code=400)
    register_push_token(user["username"], token)
    return {"ok": True}


# ---------------------------------------------------------------------------
# پنل مدیریت (بدون نیاز به SSH) — فقط برای کاربران با level >= 3
# ---------------------------------------------------------------------------
def admin_layout(title: str, body_html: str) -> str:
    return f"""
    <html lang="fa" dir="rtl">
    <head>
        <meta charset="utf-8">
        <title>{title} — مدیریت ValveBox</title>
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            * {{ box-sizing:border-box; }}
            body {{ margin:0; background:#0b1220; color:#e2e8f0; font-family:Tahoma, Arial, sans-serif; }}
            .topbar {{
                display:flex; gap:16px; align-items:center; padding:12px 18px;
                border-bottom:1px solid #1f2937; background:#111827; flex-wrap:wrap;
            }}
            .topbar a {{ color:#93c5fd; text-decoration:none; font-size:13px; }}
            .container {{ max-width:800px; margin:0 auto; padding:20px; }}
            table {{ width:100%; border-collapse:collapse; margin-bottom:24px; }}
            th, td {{ text-align:right; padding:8px 10px; border-bottom:1px solid #1f2937; font-size:13px; }}
            th {{ color:#94a3b8; }}
            input, select {{
                width:100%; padding:8px 10px; margin-bottom:10px;
                border-radius:8px; border:1px solid #334155; background:#0b1220; color:#e2e8f0;
                font-family: Tahoma, Arial, sans-serif; font-size:13px;
            }}
            button {{
                padding:8px 14px; border:none; border-radius:8px;
                background:#2563eb; color:#fff; font-weight:700; cursor:pointer; font-size:13px;
            }}
            button.danger {{ background:#dc2626; }}
            .card {{ background:#111827; border:1px solid #1f2937; border-radius:14px; padding:18px; margin-bottom:20px; }}
            h2 {{ font-size:16px; }}
        </style>
    </head>
    <body>
        <div class="topbar">
            <strong>مدیریت ValveBox</strong>
            <a href="/admin">داشبورد</a>
            <a href="/admin/devices">دستگاه‌ها</a>
            <a href="/admin/users">کاربران</a>
            <a href="/admin/backup">پشتیبان‌گیری</a>
            <a href="/admin/audit">لاگ فعالیت</a>
            <a href="/admin/report">گزارش هوشمند</a>
            <a href="/devices">بازگشت به هاب</a>
        </div>
        <div class="container">{body_html}</div>
    </body>
    </html>
    """


# ---------------------------------------------------------------------------
# تأیید دوباره‌ی رمز عبور برای ورود به پنل مدیریت (حتی اگر از قبل لاگین
# باشی). این تأیید تا ADMIN_REVERIFY_MINUTES دقیقه معتبر می‌ماند.
# ---------------------------------------------------------------------------
def admin_verify_html(next_path: str, error: str = "") -> str:
    err_html = f'<div style="color:#f87171;margin-bottom:10px;font-size:13px;">{error}</div>' if error else ""
    return f"""
    <html lang="fa" dir="rtl">
    <head>
        <meta charset="utf-8">
        <title>تأیید هویت مدیر — ValveBox</title>
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            body {{
                margin:0; background:#0b1220; color:#e2e8f0;
                font-family: Tahoma, Arial, sans-serif;
                display:flex; align-items:center; justify-content:center; height:100vh;
            }}
            .box {{
                background:#111827; border:1px solid #1f2937; border-radius:14px;
                padding:28px; width:100%; max-width:360px;
            }}
            h1 {{ font-size:18px; margin-top:0; }}
            p.hint {{ font-size:13px; color:#94a3b8; }}
            input {{
                width:100%; padding:10px 12px; margin-bottom:12px;
                border-radius:8px; border:1px solid #334155; background:#0b1220; color:#e2e8f0;
                font-family: Tahoma, Arial, sans-serif; font-size:14px;
            }}
            button {{
                width:100%; padding:10px; border:none; border-radius:8px;
                background:#2563eb; color:#fff; font-weight:700; cursor:pointer;
            }}
        </style>
    </head>
    <body>
        <form class="box" method="post" action="/admin/verify">
            <h1>ورود به پنل مدیریت</h1>
            <p class="hint">برای دسترسی به بخش مدیریت، رمز عبور خودت را دوباره وارد کن.</p>
            {err_html}
            <input type="hidden" name="next" value="{next_path}">
            <input name="password" type="password" placeholder="رمز عبور" autofocus>
            <button type="submit">تأیید</button>
        </form>
    </body>
    </html>
    """


@app.get("/admin/verify", response_class=HTMLResponse)
async def admin_verify_page(request: Request, next: str = "/admin"):
    user = current_user(request)
    if not user or int(user.get("level", 0)) < 3:
        return HTMLResponse("<h2>دسترسی غیرمجاز — این بخش فقط برای مدیر است.</h2>", status_code=403)
    return HTMLResponse(admin_verify_html(next))


@app.post("/admin/verify")
async def admin_verify_submit(request: Request, password: str = Form(...), next: str = Form("/admin")):
    user = current_user(request)
    if not user or int(user.get("level", 0)) < 3:
        return HTMLResponse("<h2>دسترسی غیرمجاز — این بخش فقط برای مدیر است.</h2>", status_code=403)

    users = load_users()
    info = users.get(user["username"])
    if not info or info.get("password") != hash_pw(password):
        log_audit(user["username"], "admin_verify_failed")
        return HTMLResponse(admin_verify_html(next, "رمز عبور نادرست است."))

    request.session["admin_verified_at"] = time.time()
    log_audit(user["username"], "admin_verify_success")
    return RedirectResponse(url=next, status_code=303)


@app.get("/admin", response_class=HTMLResponse)
async def admin_dashboard(request: Request):
    user, err = require_admin(request)
    if err:
        return err
    all_devices = load_devices()
    all_users = load_users()
    body = f"""
    <div class="card">
        <h2>خلاصه</h2>
        <p>تعداد دستگاه‌ها: {len(all_devices)}</p>
        <p>تعداد کاربران: {len(all_users)}</p>
    </div>
    """
    return HTMLResponse(admin_layout("داشبورد", body))


# --- مدیریت دستگاه‌ها ---
@app.get("/admin/devices", response_class=HTMLResponse)
async def admin_devices_page(request: Request):
    user, err = require_admin(request)
    if err:
        return err
    all_devices = load_devices()
    rows = ""
    for dev_id, info in all_devices.items():
        rows += f"""
        <tr>
            <td>{dev_id}</td>
            <td>{info.get('name','')}</td>
            <td>{info.get('zone','')}</td>
            <td>{info.get('hospital','')}</td>
            <td>{info.get('public_url','')}</td>
            <td>
                <form method="post" action="/admin/devices/delete/{dev_id}" onsubmit="return confirm('حذف شود؟');">
                    <button class="danger" type="submit">حذف</button>
                </form>
            </td>
        </tr>
        """
    body = f"""
    <div class="card">
        <h2>دستگاه‌ها</h2>
        <table>
            <tr><th>شناسه</th><th>نام</th><th>محل</th><th>بیمارستان</th><th>آدرس عمومی</th><th></th></tr>
            {rows}
        </table>
    </div>
    <div class="card">
        <h2>افزودن دستگاه جدید</h2>
        <form method="post" action="/admin/devices/add">
            <input name="device_id" placeholder="شناسه‌ی یکتا (مثلاً icu3)" required>
            <input name="name" placeholder="نام نمایشی" required>
            <input name="zone" placeholder="محل / بخش">
            <input name="hospital" placeholder="کلید بیمارستان (مثلاً hospital1)" required>
            <input name="public_url" placeholder="آدرس عمومی دستگاه (https://...)">
            <input name="token" placeholder="توکن ارتباطی دستگاه" required>
            <button type="submit">افزودن</button>
        </form>
    </div>
    """
    return HTMLResponse(admin_layout("دستگاه‌ها", body))


@app.post("/admin/devices/add")
async def admin_devices_add(
    request: Request,
    device_id: str = Form(...),
    name: str = Form(...),
    zone: str = Form(""),
    hospital: str = Form(...),
    public_url: str = Form(""),
    token: str = Form(...),
):
    user, err = require_admin(request)
    if err:
        return err
    devices = load_devices()
    devices[device_id.strip()] = {
        "name": name.strip(),
        "zone": zone.strip(),
        "hospital": hospital.strip(),
        "public_url": public_url.strip(),
        "token": token.strip(),
    }
    with open(DEVICES_FILE, "w", encoding="utf-8") as f:
        json.dump(devices, f, ensure_ascii=False, indent=2)
    log_audit(user["username"], "device_add", device_id)
    return RedirectResponse(url="/admin/devices", status_code=303)


@app.post("/admin/devices/delete/{device_id}")
async def admin_devices_delete(request: Request, device_id: str):
    user, err = require_admin(request)
    if err:
        return err
    devices = load_devices()
    devices.pop(device_id, None)
    with open(DEVICES_FILE, "w", encoding="utf-8") as f:
        json.dump(devices, f, ensure_ascii=False, indent=2)
    log_audit(user["username"], "device_delete", device_id)
    return RedirectResponse(url="/admin/devices", status_code=303)


# --- مدیریت کاربران ---
@app.get("/admin/users", response_class=HTMLResponse)
async def admin_users_page(request: Request):
    user, err = require_admin(request)
    if err:
        return err
    all_users = load_users()
    rows = ""
    for uname, info in all_users.items():
        level_options = "".join(
            f'<option value="{lv}" {"selected" if int(info.get("level",1))==lv else ""}>{lv}</option>'
            for lv in (1, 2, 3)
        )
        rows += f"""
        <tr>
            <td>{uname}</td>
            <td>{info.get('name','')}</td>
            <td>{info.get('hospital','')}</td>
            <td>
                <form method="post" action="/admin/users/set-level/{uname}" style="display:flex;gap:6px;">
                    <select name="level" style="margin:0;">{level_options}</select>
                    <button type="submit">ثبت</button>
                </form>
            </td>
            <td>{info.get('email','')}</td>
            <td>
                <form method="post" action="/admin/users/delete/{uname}" onsubmit="return confirm('حذف شود؟');">
                    <button class="danger" type="submit">حذف</button>
                </form>
            </td>
        </tr>
        """
    body = f"""
    <div class="card">
        <h2>کاربران</h2>
        <p style="color:#94a3b8;font-size:12px;">سطح ۳ = دسترسی به پنل مدیریت. برای تغییر سطح یک کاربر، از منوی کنار اسمش استفاده کن و «ثبت» بزن.</p>
        <table>
            <tr><th>یوزرنیم</th><th>نام</th><th>بیمارستان</th><th>سطح دسترسی</th><th>ایمیل</th><th></th></tr>
            {rows}
        </table>
    </div>
    <div class="card">
        <h2>افزودن کاربر جدید</h2>
        <form method="post" action="/admin/users/add">
            <input name="username" placeholder="یوزرنیم" required>
            <input name="password" placeholder="رمز عبور (متن ساده — خودکار هش می‌شود)" required>
            <input name="name" placeholder="نام نمایشی" required>
            <input name="hospital" placeholder="کلید بیمارستان (مثلاً hospital1)" required>
            <input name="email" placeholder="ایمیل (برای بازیابی رمز)">
            <select name="level">
                <option value="1">۱ — کاربر عادی</option>
                <option value="2">۲ — سرپرست</option>
                <option value="3">۳ — مدیر</option>
            </select>
            <input name="devices" placeholder='دستگاه‌ها: ALL یا لیست با کاما (icu1,icu2)' value="ALL">
            <button type="submit">افزودن</button>
        </form>
    </div>
    """
    return HTMLResponse(admin_layout("کاربران", body))


@app.post("/admin/users/add")
async def admin_users_add(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    name: str = Form(...),
    hospital: str = Form(...),
    email: str = Form(""),
    level: int = Form(1),
    devices: str = Form("ALL"),
):
    user, err = require_admin(request)
    if err:
        return err
    users = load_users()
    devices_value = "ALL" if devices.strip().upper() == "ALL" else [d.strip() for d in devices.split(",") if d.strip()]
    users[username.strip()] = {
        "name": name.strip(),
        "hospital": hospital.strip(),
        "password": hash_pw(password),
        "level": int(level),
        "devices": devices_value,
        "email": email.strip(),
    }
    save_users(users)
    log_audit(user["username"], "user_add", username)
    return RedirectResponse(url="/admin/users", status_code=303)


@app.post("/admin/users/delete/{username}")
async def admin_users_delete(request: Request, username: str):
    user, err = require_admin(request)
    if err:
        return err
    users = load_users()
    users.pop(username, None)
    save_users(users)
    log_audit(user["username"], "user_delete", username)
    return RedirectResponse(url="/admin/users", status_code=303)


@app.post("/admin/users/set-level/{username}")
async def admin_users_set_level(request: Request, username: str, level: int = Form(...)):
    """تغییر سطح دسترسی یک کاربر — برای دادن یا گرفتن دسترسی پنل مدیریت
    (سطح ۳)، بدون نیاز به حذف و ساخت دوباره‌ی کاربر."""
    user, err = require_admin(request)
    if err:
        return err
    users = load_users()
    if username not in users:
        return HTMLResponse("<h2>کاربر پیدا نشد.</h2>", status_code=404)
    users[username]["level"] = int(level)
    save_users(users)
    log_audit(user["username"], "user_set_level", f"{username} -> level {level}")
    return RedirectResponse(url="/admin/users", status_code=303)


# --- پشتیبان‌گیری ---
@app.get("/admin/backup", response_class=HTMLResponse)
async def admin_backup_page(request: Request):
    user, err = require_admin(request)
    if err:
        return err
    backups = list_backups()
    rows = "".join(
        f"""<tr>
            <td>{b['name']}</td><td>{b['size_kb']} KB</td>
            <td><a href="/admin/backup/download/{b['name']}">دانلود</a></td>
        </tr>"""
        for b in backups
    )
    body = f"""
    <div class="card">
        <h2>پشتیبان‌گیری</h2>
        <p>هر روز به‌صورت خودکار یک نسخه‌ی پشتیبان ساخته می‌شود (تا {BACKUP_KEEP_DAYS} روز نگه‌داری می‌شود).</p>
        <form method="post" action="/admin/backup/run">
            <button type="submit">ساخت پشتیبان همین الان</button>
        </form>
        <table style="margin-top:16px;">
            <tr><th>نام فایل</th><th>حجم</th><th></th></tr>
            {rows if rows else '<tr><td colspan="3">هنوز پشتیبانی ساخته نشده.</td></tr>'}
        </table>
    </div>
    """
    return HTMLResponse(admin_layout("پشتیبان‌گیری", body))


@app.post("/admin/backup/run")
async def admin_backup_run(request: Request):
    user, err = require_admin(request)
    if err:
        return err
    run_backup()
    log_audit(user["username"], "backup_run")
    return RedirectResponse(url="/admin/backup", status_code=303)


@app.get("/admin/backup/download/{filename}")
async def admin_backup_download(request: Request, filename: str):
    user, err = require_admin(request)
    if err:
        return err
    path = BACKUP_DIR / filename
    if not path.exists() or not filename.startswith("backup_"):
        return HTMLResponse("<h2>فایل پیدا نشد.</h2>", status_code=404)
    from fastapi.responses import FileResponse
    return FileResponse(path, filename=filename)


# --- لاگ فعالیت ---
@app.get("/admin/audit", response_class=HTMLResponse)
async def admin_audit_page(request: Request):
    user, err = require_admin(request)
    if err:
        return err
    entries = fetch_audit_log()
    rows = "".join(
        f"<tr><td>{e['timestamp']}</td><td>{e['username'] or '—'}</td><td>{e['action']}</td><td>{e['detail'] or ''}</td></tr>"
        for e in entries
    )
    body = f"""
    <div class="card">
        <h2>لاگ فعالیت (۲۰۰ مورد آخر)</h2>
        <table>
            <tr><th>زمان</th><th>کاربر</th><th>عملیات</th><th>جزئیات</th></tr>
            {rows if rows else '<tr><td colspan="4">هنوز فعالیتی ثبت نشده.</td></tr>'}
        </table>
    </div>
    """
    return HTMLResponse(admin_layout("لاگ فعالیت", body))


# --- گزارش هوشمند ---
@app.get("/admin/report", response_class=HTMLResponse)
async def admin_report_page(request: Request, device_id: str = "", start: str = "", end: str = ""):
    user, err = require_admin(request)
    if err:
        return err
    all_devices = load_devices()
    options = "".join(
        f'<option value="{dev_id}" {"selected" if dev_id==device_id else ""}>{info.get("name", dev_id)}</option>'
        for dev_id, info in all_devices.items()
    )

    result_html = ""
    if device_id and start and end:
        # فیلدهای datetime-local مقداری مثل "2026-08-19T14:30" می‌دهند؛
        # باید T را به فاصله تبدیل کنیم تا با فرمت ذخیره‌شده در دیتابیس
        # ("2026-08-19 14:30:00") هم‌خوان شود.
        start_norm = start.replace("T", " ")
        end_norm = end.replace("T", " ")
        stats = build_report_stats(device_id, start_norm, end_norm)
        log_audit(user["username"], "report_view", device_id)
        narrative = build_ai_narrative(stats)
        narrative_html = f'<div class="card"><h2>گزارش روایی</h2><p style="white-space:pre-line;">{narrative}</p></div>' if narrative else '<p style="color:#94a3b8;">برای گزارش روایی هوشمند، کلید ANTHROPIC_API_KEY را در .env تنظیم کن (اختیاری، هزینه دارد). آمار خام رایگان زیر است.</p>'

        per_gas_rows = "".join(
            f"<tr><td>{gas}</td><td>{d['count']}</td></tr>" for gas, d in stats["per_gas"].items()
        )
        avg_min = round(stats["avg_duration_sec"] / 60, 1) if stats["avg_duration_sec"] else "—"
        max_min = round(stats["max_duration_sec"] / 60, 1) if stats["max_duration_sec"] else "—"

        result_html = f"""
        {narrative_html}
        <div class="card">
            <h2>آمار خام ({stats['total_events']} رویداد)</h2>
            <table>
                <tr><th>گاز</th><th>تعداد آلارم</th></tr>
                {per_gas_rows if per_gas_rows else '<tr><td colspan="2">رویدادی در این بازه نیست.</td></tr>'}
            </table>
            <p>میانگین مدت آلارم: {avg_min} دقیقه — حداکثر: {max_min} دقیقه</p>
        </div>
        """

    body = f"""
    <div class="card">
        <h2>گزارش تاریخچه‌ی یک دستگاه</h2>
        <form method="get" action="/admin/report">
            <select name="device_id">{options}</select>
            <input type="datetime-local" name="start" value="{start}" required>
            <input type="datetime-local" name="end" value="{end}" required>
            <button type="submit">ساخت گزارش</button>
        </form>
    </div>
    {result_html}
    """
    return HTMLResponse(admin_layout("گزارش هوشمند", body))


# ---------------------------------------------------------------------------
# پشتیبان‌گیری خودکار روزانه (در پس‌زمینه، بدون نیاز به cron جدا)
# ---------------------------------------------------------------------------
@app.on_event("startup")
async def _schedule_daily_backup():
    import asyncio

    async def loop():
        while True:
            try:
                run_backup()
            except Exception as e:
                print(f"[خطا] پشتیبان‌گیری خودکار ناموفق بود: {e}")
            try:
                prune_old_gas_readings()
            except Exception as e:
                print(f"[خطا] پاک‌سازی داده‌های قدیمی روند گاز ناموفق بود: {e}")
            await asyncio.sleep(24 * 60 * 60)

    asyncio.create_task(loop())
