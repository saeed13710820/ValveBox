package ir.valvebox.app.work

import android.app.NotificationManager
import androidx.core.app.NotificationCompat
import com.google.firebase.messaging.FirebaseMessagingService
import com.google.firebase.messaging.RemoteMessage
import ir.valvebox.app.network.ApiClient
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch

/**
 * دریافت اعلان‌های فوری از Firebase Cloud Messaging. اگر پروژه‌ی Firebase
 * راه‌اندازی نشده باشد (google-services.json وجود نداشته باشد)، این کلاس
 * اصلاً فراخوانی نمی‌شود — اپ بدون خطا و بدون پوش فوری کار می‌کند.
 */
class PushService : FirebaseMessagingService() {

    override fun onNewToken(token: String) {
        super.onNewToken(token)
        CoroutineScope(Dispatchers.IO).launch {
            try {
                ApiClient.registerPushToken(token)
            } catch (e: Exception) {
                // اگر هنوز لاگین نشده یا شبکه در دسترس نیست، مشکلی نیست —
                // توکن بعد از ورود موفق دوباره ثبت می‌شود.
            }
        }
    }

    override fun onMessageReceived(message: RemoteMessage) {
        super.onMessageReceived(message)
        ensureAlarmNotificationChannel(applicationContext)

        val title = message.notification?.title ?: "هشدار ولوباکس"
        val body = message.notification?.body ?: ""

        val nm = getSystemService(NotificationManager::class.java)
        val builder = NotificationCompat.Builder(this, ALARM_NOTIFICATION_CHANNEL_ID)
            .setSmallIcon(android.R.drawable.ic_dialog_alert)
            .setContentTitle(title)
            .setContentText(body)
            .setPriority(NotificationCompat.PRIORITY_HIGH)
            .setAutoCancel(true)

        nm.notify(System.currentTimeMillis().toInt(), builder.build())
    }
}
