
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
        return JSONResponse({"error": "نام بیمارستان، نام کاربری یا رمز عبور نادرست است."}, status_code=401)

    request.session["user"] = {
        "username": username,
        "name": info.get("name", username),
        "hospital": hospital_key,
        "hospital_name": hospitals.get(hospital_key, {}).get("name", hospital_key),
        "devices": info.get("devices", []),
    }
    return {"ok": True, "name": info.get("name", username), "hospital_name": hospitals.get(hospital_key, {}).get("name", hospital_key)}


# ---------------------------------------------------------------------------
# API JSON برای تاریخچه‌ی آلارم‌های یک دستگاه (برای اپ اندروید).
#
# فرض: همان دیتابیس events.db که در weekly_report.py استفاده شده، از داخل
# app.py هم در دسترس است. اگر مسیر فایل دیتابیس هاب شما جای دیگری است، فقط
# مقدار EVENTS_DB_PATH را پایین همین بخش عوض کن.
# ---------------------------------------------------------------------------
import sqlite3 as _sqlite3

EVENTS_DB_PATH = "events.db"  # در صورت نیاز، مسیر واقعی فایل events.db را اینجا بگذار


@app.get("/api/device/{device_id}/history")
async def device_history_api(request: Request, device_id: str):
    user, err = require_login(request)
    if err:
        return err
    all_devices = load_devices()
    if not user_can_see(user, device_id, all_devices):
        return JSONResponse({"error": "Forbidden"}, status_code=403)

    try:
        conn = _sqlite3.connect(EVENTS_DB_PATH)
        rows = conn.execute(
            """
            SELECT gas_type, start_time, end_time, resolved_by, duration_sec
            FROM events
            WHERE device_id = ?
            ORDER BY start_time DESC
            LIMIT 100
            """,
            (device_id,),
        ).fetchall()
        conn.close()
    except Exception as e:
        return JSONResponse({"error": f"خطای دیتابیس: {e}"}, status_code=500)

    events = [
        {
            "gas_type": r[0],
            "start_time": r[1],
            "end_time": r[2],
            "resolved_by": r[3],
            "duration_sec": r[4],
        }
        for r in rows
    ]
    return {"events": events}
