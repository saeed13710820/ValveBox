package ir.valvebox.app.network

import android.content.Context
import android.content.SharedPreferences
import okhttp3.Cookie
import okhttp3.CookieJar
import okhttp3.HttpUrl
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONObject
import java.util.concurrent.TimeUnit

/**
 * آدرس هاب. اگر دامنه یا پورت متفاوتی داری، همین یک خط را عوض کن.
 * مثال محلی: "http://192.168.1.50:9000"
 */
object HubConfig {
    var baseUrl: String = "https://valvebox.ir"
}

/**
 * نگه‌داری کوکی نشست (session) در حافظه‌ی دائمی گوشی (SharedPreferences)،
 * نه فقط در حافظه‌ی موقت برنامه. این یعنی حتی بعد از بستن کامل اپ، دوباره
 * که بازش کنی نیازی به ورود مجدد نیست (تا وقتی نشست سمت سرور معتبر باشد).
 */
private class PersistentCookieJar(context: Context) : CookieJar {
    private val prefs: SharedPreferences =
        context.applicationContext.getSharedPreferences("valvebox_cookies", Context.MODE_PRIVATE)

    override fun saveFromResponse(url: HttpUrl, cookies: List<Cookie>) {
        if (cookies.isEmpty()) return
        val editor = prefs.edit()
        for (c in cookies) {
            val key = "${url.host}|${c.name}"
            editor.putString(key, c.toString())
        }
        editor.apply()
    }

    override fun loadForRequest(url: HttpUrl): List<Cookie> {
        val result = mutableListOf<Cookie>()
        for ((key, value) in prefs.all) {
            if (key.startsWith("${url.host}|") && value is String) {
                Cookie.parse(url, value)?.let { result.add(it) }
            }
        }
        return result
    }

    fun clear() {
        prefs.edit().clear().apply()
    }
}

object ApiClient {
    private lateinit var cookieJar: PersistentCookieJar
    private var initialized = false

    /** باید یک بار، در همان ابتدای اجرای اپ (MainActivity) صدا زده شود. */
    fun init(context: Context) {
        if (initialized) return
        cookieJar = PersistentCookieJar(context)
        initialized = true
    }

    val client: OkHttpClient by lazy {
        OkHttpClient.Builder()
            .cookieJar(cookieJar)
            .connectTimeout(15, TimeUnit.SECONDS)
            .readTimeout(15, TimeUnit.SECONDS)
            .build()
    }

    fun clearSession() = cookieJar.clear()

    /** آیا از قبل یک نشست ذخیره‌شده داریم؟ (برای تصمیم "برو مستقیم به لیست دستگاه‌ها یا صفحه‌ی ورود") */
    fun hasSavedSession(context: Context): Boolean {
        val prefs = context.applicationContext.getSharedPreferences("valvebox_cookies", Context.MODE_PRIVATE)
        return prefs.all.keys.any { it.contains("|session") || it.contains("|Session") }
    }

    private val JSON = "application/json; charset=utf-8".toMediaType()

    /** POST /api/login */
    fun login(hospital: String, username: String, password: String): Result<String> {
        val body = JSONObject().apply {
            put("hospital", hospital)
            put("username", username)
            put("password", password)
        }.toString().toRequestBody(JSON)

        val req = Request.Builder()
            .url("${HubConfig.baseUrl}/api/login")
            .post(body)
            .build()

        client.newCall(req).execute().use { resp ->
            val text = resp.body?.string() ?: ""
            return if (resp.isSuccessful) {
                Result.success(text)
            } else {
                val msg = try { JSONObject(text).optString("error", "ورود ناموفق بود.") }
                          catch (e: Exception) { "ورود ناموفق بود." }
                Result.failure(Exception(msg))
            }
        }
    }

    /** GET /api/devices */
    fun fetchDevices(): Result<String> {
        val req = Request.Builder()
            .url("${HubConfig.baseUrl}/api/devices")
            .get()
            .build()
        client.newCall(req).execute().use { resp ->
            val text = resp.body?.string() ?: ""
            return if (resp.isSuccessful) Result.success(text)
            else Result.failure(Exception("دریافت لیست دستگاه‌ها ناموفق بود (${resp.code})"))
        }
    }

    /** GET /api/device/{id}/data */
    fun fetchDeviceData(deviceId: String): Result<String> {
        val req = Request.Builder()
            .url("${HubConfig.baseUrl}/api/device/$deviceId/data")
            .get()
            .build()
        client.newCall(req).execute().use { resp ->
            val text = resp.body?.string() ?: ""
            return if (resp.isSuccessful) Result.success(text)
            else Result.failure(Exception("دریافت وضعیت دستگاه ناموفق بود (${resp.code})"))
        }
    }

    /** GET /api/device/{id}/history */
    fun fetchDeviceHistory(deviceId: String): Result<String> {
        val req = Request.Builder()
            .url("${HubConfig.baseUrl}/api/device/$deviceId/history")
            .get()
            .build()
        client.newCall(req).execute().use { resp ->
            val text = resp.body?.string() ?: ""
            return if (resp.isSuccessful) Result.success(text)
            else Result.failure(Exception("دریافت تاریخچه ناموفق بود (${resp.code})"))
        }
    }

    /** POST /api/register-push-token — ثبت توکن Firebase برای اعلان فوری */
    fun registerPushToken(token: String): Result<Unit> {
        val body = JSONObject().apply {
            put("token", token)
        }.toString().toRequestBody(JSON)

        val req = Request.Builder()
            .url("${HubConfig.baseUrl}/api/register-push-token")
            .post(body)
            .build()

        client.newCall(req).execute().use { resp ->
            return if (resp.isSuccessful) Result.success(Unit)
            else Result.failure(Exception("ثبت توکن پوش ناموفق بود (${resp.code})"))
        }
    }

    /** ثبت اینکه آیا کاربر لاگین‌شده سطح مدیر دارد یا نه (برای نمایش دکمه‌ی
     *  «پنل مدیریت» در اپ). در حافظه‌ی دائمی ذخیره می‌شود تا بعد از بستن و
     *  باز کردن دوباره‌ی اپ هم (وقتی لاگین خودکار انجام می‌شود) این اطلاعات
     *  از دست نرود. */
    fun setAdminFlag(context: Context, isAdmin: Boolean) {
        context.applicationContext
            .getSharedPreferences("valvebox_meta", Context.MODE_PRIVATE)
            .edit().putBoolean("is_admin", isAdmin).apply()
    }

    fun isAdmin(context: Context): Boolean {
        return context.applicationContext
            .getSharedPreferences("valvebox_meta", Context.MODE_PRIVATE)
            .getBoolean("is_admin", false)
    }

    /** کوکی نشستی که اپ برای درخواست‌های شبکه‌اش استفاده می‌کند را به
     *  CookieManager خودِ WebView کپی می‌کند — تا وقتی پنل مدیریت (که یک
     *  صفحه‌ی وب است) داخل اپ باز می‌شود، از همان ورود قبلی استفاده کند و
     *  کاربر مجبور به ورود دوباره نشود. */
    fun syncCookiesToWebView(context: Context) {
        val host = try {
            android.net.Uri.parse(HubConfig.baseUrl).host
        } catch (e: Exception) {
            null
        } ?: return

        val prefs = context.applicationContext.getSharedPreferences("valvebox_cookies", Context.MODE_PRIVATE)
        val cookieManager = android.webkit.CookieManager.getInstance()
        cookieManager.setAcceptCookie(true)
        for ((key, value) in prefs.all) {
            if (key.startsWith("$host|") && value is String) {
                cookieManager.setCookie(HubConfig.baseUrl, value)
            }
        }
        cookieManager.flush()
    }
    fun logout() {
        val req = Request.Builder().url("${HubConfig.baseUrl}/logout").get().build()
        try { client.newCall(req).execute().close() } catch (e: Exception) { /* ignore */ }
        clearSession()
    }

    /** POST /api/forgot-password */
    fun forgotPassword(username: String): Result<String> {
        val body = JSONObject().apply {
            put("username", username)
        }.toString().toRequestBody(JSON)

        val req = Request.Builder()
            .url("${HubConfig.baseUrl}/api/forgot-password")
            .post(body)
            .build()

        client.newCall(req).execute().use { resp ->
            val text = resp.body?.string() ?: ""
            return if (resp.isSuccessful) {
                val msg = try { JSONObject(text).optString("message", "درخواست ارسال شد.") }
                          catch (e: Exception) { "درخواست ارسال شد." }
                Result.success(msg)
            } else {
                Result.failure(Exception("ارسال درخواست ناموفق بود (${resp.code})"))
            }
        }
    }
}
