package ir.valvebox.app

import android.Manifest
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.os.Bundle
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.runtime.mutableStateOf
import androidx.compose.ui.Modifier
import androidx.compose.foundation.layout.fillMaxSize
import androidx.core.content.ContextCompat
import androidx.fragment.app.FragmentActivity
import androidx.navigation.NavType
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.rememberNavController
import androidx.navigation.navArgument
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import ir.valvebox.app.model.DeviceInfo
import ir.valvebox.app.network.ApiClient
import ir.valvebox.app.ui.DeviceScreen
import ir.valvebox.app.ui.DevicesScreen
import ir.valvebox.app.ui.ForgotPasswordScreen
import ir.valvebox.app.ui.FullControlScreen
import ir.valvebox.app.ui.HistoryScreen
import ir.valvebox.app.ui.LockScreen
import ir.valvebox.app.ui.LoginScreen
import ir.valvebox.app.work.AlarmCheckWorker
import ir.valvebox.app.work.ensureAlarmNotificationChannel
import java.util.concurrent.TimeUnit

class MainActivity : FragmentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        ApiClient.init(applicationContext)
        ensureAlarmNotificationChannel(applicationContext)
        requestNotificationPermissionIfNeeded()
        schedulePeriodicAlarmCheck()

        setContent {
            MaterialTheme {
                Surface(modifier = Modifier.fillMaxSize()) {
                    ValveBoxApp()
                }
            }
        }
    }

    private val notificationPermissionLauncher =
        registerForActivityResult(ActivityResultContracts.RequestPermission()) { /* نتیجه لازم نیست */ }

    private fun requestNotificationPermissionIfNeeded() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            if (ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS)
                != PackageManager.PERMISSION_GRANTED
            ) {
                notificationPermissionLauncher.launch(Manifest.permission.POST_NOTIFICATIONS)
            }
        }
    }

    private fun schedulePeriodicAlarmCheck() {
        val request = PeriodicWorkRequestBuilder<AlarmCheckWorker>(15, TimeUnit.MINUTES).build()
        WorkManager.getInstance(applicationContext).enqueueUniquePeriodicWork(
            "valvebox_alarm_check",
            ExistingPeriodicWorkPolicy.KEEP,
            request
        )
    }
}

/**
 * نگه‌داری موقت اطلاعات دستگاه انتخاب‌شده بین صفحات ناوبری، چون
 * NavController فقط رشته/عدد ساده را به‌راحتی بین صفحات پاس می‌دهد.
 */
private val selectedDevice = mutableStateOf<DeviceInfo?>(null)

@androidx.compose.runtime.Composable
fun ValveBoxApp() {
    val navController = rememberNavController()
    val context = androidx.compose.ui.platform.LocalContext.current
    val startDestination = if (ApiClient.hasSavedSession(context)) "lock" else "login"

    NavHost(navController = navController, startDestination = startDestination) {
        composable("login") {
            LoginScreen(
                onLoginSuccess = {
                    navController.navigate("devices") {
                        popUpTo("login") { inclusive = true }
                    }
                },
                onForgotPassword = { navController.navigate("forgot_password") }
            )
        }

        composable("forgot_password") {
            ForgotPasswordScreen(onBack = { navController.popBackStack() })
        }

        composable("lock") {
            LockScreen(onUnlocked = {
                navController.navigate("devices") {
                    popUpTo("lock") { inclusive = true }
                }
            })
        }

        composable("devices") {
            DevicesScreen(
                onOpenDevice = { dev ->
                    selectedDevice.value = dev
                    navController.navigate("device")
                },
                onLogout = {
                    navController.navigate("login") {
                        popUpTo("devices") { inclusive = true }
                    }
                },
                onOpenAdminPanel = { navController.navigate("admin_panel") }
            )
        }

        composable("admin_panel") {
            FullControlScreen(
                url = "${ir.valvebox.app.network.HubConfig.baseUrl}/admin",
                title = "پنل مدیریت",
                syncSessionCookie = true,
                onBack = { navController.popBackStack() }
            )
        }

        composable("device") {
            val dev = selectedDevice.value
            if (dev != null) {
                DeviceScreen(
                    device = dev,
                    onBack = { navController.popBackStack() },
                    onOpenFullControl = { url ->
                        val encoded = Uri.encode(url)
                        navController.navigate("fullcontrol/$encoded")
                    },
                    onOpenHistory = { navController.navigate("history") },
                    onOpenTrends = { navController.navigate("trends") }
                )
            }
        }

        composable("history") {
            val dev = selectedDevice.value
            if (dev != null) {
                HistoryScreen(
                    deviceId = dev.deviceId,
                    deviceName = dev.name,
                    onBack = { navController.popBackStack() }
                )
            }
        }

        composable("trends") {
            val dev = selectedDevice.value
            if (dev != null) {
                FullControlScreen(
                    url = "${ir.valvebox.app.network.HubConfig.baseUrl}/trends/${dev.deviceId}",
                    title = "روند گاز — ${dev.name}",
                    syncSessionCookie = true,
                    onBack = { navController.popBackStack() }
                )
            }
        }

        composable(
            route = "fullcontrol/{url}",
            arguments = listOf(navArgument("url") { type = NavType.StringType })
        ) { backStackEntry ->
            val encodedUrl = backStackEntry.arguments?.getString("url") ?: ""
            val url = Uri.decode(encodedUrl)
            FullControlScreen(url = url, onBack = { navController.popBackStack() })
        }
    }
}
