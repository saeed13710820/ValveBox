package ir.valvebox.app.work

import android.Manifest
import android.app.NotificationChannel
import android.app.NotificationManager
import android.content.Context
import android.content.pm.PackageManager
import android.os.Build
import androidx.core.app.ActivityCompat
import androidx.core.app.NotificationCompat
import androidx.work.CoroutineWorker
import androidx.work.WorkerParameters
import ir.valvebox.app.model.parseDeviceDataResponse
import ir.valvebox.app.model.parseDevicesResponse
import ir.valvebox.app.network.ApiClient
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

const val ALARM_NOTIFICATION_CHANNEL_ID = "valvebox_alarms"

/**
 * هر بار که اجرا می‌شود (طبق زمان‌بندی WorkManager، حداقل هر ۱۵ دقیقه چون
 * این محدودیت خودِ اندروید است): لیست دستگاه‌ها را می‌گیرد، برای هرکدام
 * وضعیت لحظه‌ای گازها را چک می‌کند و اگر گازی در حالت آلارم (low/high/fault)
 * بود، یک اعلان محلی نشان می‌دهد.
 *
 * توجه: این یک پوش نوتیفیکیشن واقعی (Firebase) نیست — بررسی دوره‌ای است،
 * پس ممکن است تا ۱۵ دقیقه تأخیر داشته باشد.
 */
class AlarmCheckWorker(appContext: Context, params: WorkerParameters) :
    CoroutineWorker(appContext, params) {

    override suspend fun doWork(): Result = withContext(Dispatchers.IO) {
        ApiClient.init(applicationContext)

        val devicesResult = ApiClient.fetchDevices()
        val devicesJson = devicesResult.getOrNull() ?: return@withContext Result.success()
        val devices = parseDevicesResponse(devicesJson)

        val alarming = mutableListOf<String>()

        for (dev in devices) {
            val dataResult = ApiClient.fetchDeviceData(dev.deviceId)
            val dataJson = dataResult.getOrNull() ?: continue
            val data = parseDeviceDataResponse(dataJson)
            val bad = data.gauges.filter { it.alarm != "ok" }
            if (bad.isNotEmpty()) {
                val names = bad.joinToString("، ") { "${it.gas} (${it.alarm.uppercase()})" }
                alarming.add("${dev.name}: $names")
            }
        }

        if (alarming.isNotEmpty()) {
            showNotification(applicationContext, alarming)
        }

        Result.success()
    }

    private fun showNotification(context: Context, lines: List<String>) {
        val nm = context.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager

        val builder = NotificationCompat.Builder(context, ALARM_NOTIFICATION_CHANNEL_ID)
            .setSmallIcon(android.R.drawable.ic_dialog_alert)
            .setContentTitle("هشدار ولوباکس")
            .setContentText(lines.first())
            .setStyle(NotificationCompat.BigTextStyle().bigText(lines.joinToString("\n")))
            .setPriority(NotificationCompat.PRIORITY_HIGH)
            .setAutoCancel(true)

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            if (ActivityCompat.checkSelfPermission(context, Manifest.permission.POST_NOTIFICATIONS)
                != PackageManager.PERMISSION_GRANTED
            ) {
                return
            }
        }
        nm.notify(1001, builder.build())
    }
}

fun ensureAlarmNotificationChannel(context: Context) {
    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
        val channel = NotificationChannel(
            ALARM_NOTIFICATION_CHANNEL_ID,
            "هشدارهای ولوباکس",
            NotificationManager.IMPORTANCE_HIGH
        ).apply {
            description = "اعلان زمانی که یکی از گازها در وضعیت آلارم باشد"
        }
        val nm = context.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        nm.createNotificationChannel(channel)
    }
}
