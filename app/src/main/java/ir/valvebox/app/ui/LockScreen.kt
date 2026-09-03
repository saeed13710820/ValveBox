package ir.valvebox.app.ui

import androidx.biometric.BiometricManager
import androidx.biometric.BiometricPrompt
import androidx.compose.foundation.layout.*
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Lock
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.core.content.ContextCompat
import androidx.fragment.app.FragmentActivity

/**
 * صفحه‌ی قفل امنیتی — قبل از نمایش هر صفحه‌ی دیگر اپ نشان داده می‌شود و
 * از اثر انگشت / پین / الگوی خود گوشی برای باز شدن استفاده می‌کند (سیستم
 * تشخیص را به عهده‌ی خود اندروید می‌گذاریم، نه یک پین جداگانه‌ی داخل اپ).
 */
@Composable
fun LockScreen(onUnlocked: () -> Unit) {
    val context = LocalContext.current
    val activity = context as? FragmentActivity

    var status by remember { mutableStateOf("در حال بررسی…") }
    var canRetry by remember { mutableStateOf(false) }

    fun startPrompt() {
        if (activity == null) {
            // اگر به هر دلیلی به Activity دسترسی نداشتیم، قفل را رد کن تا کاربر گیر نکند
            onUnlocked()
            return
        }

        val biometricManager = BiometricManager.from(activity)
        val canAuth = biometricManager.canAuthenticate(
            BiometricManager.Authenticators.BIOMETRIC_WEAK or
            BiometricManager.Authenticators.DEVICE_CREDENTIAL
        )

        if (canAuth != BiometricManager.BIOMETRIC_SUCCESS) {
            // گوشی قفل امنیتی (اثر انگشت/پین) تنظیم نکرده — بدون قفل ادامه بده
            onUnlocked()
            return
        }

        val executor = ContextCompat.getMainExecutor(activity)
        val prompt = BiometricPrompt(
            activity,
            executor,
            object : BiometricPrompt.AuthenticationCallback() {
                override fun onAuthenticationSucceeded(result: BiometricPrompt.AuthenticationResult) {
                    onUnlocked()
                }

                override fun onAuthenticationError(errorCode: Int, errString: CharSequence) {
                    status = "احراز هویت لغو شد."
                    canRetry = true
                }

                override fun onAuthenticationFailed() {
                    status = "تشخیص داده نشد — دوباره امتحان کن."
                }
            }
        )

        val promptInfo = BiometricPrompt.PromptInfo.Builder()
            .setTitle("ورود امن به ValveBox")
            .setSubtitle("برای دسترسی، هویتت را تأیید کن")
            .setAllowedAuthenticators(
                BiometricManager.Authenticators.BIOMETRIC_WEAK or
                BiometricManager.Authenticators.DEVICE_CREDENTIAL
            )
            .build()

        status = ""
        prompt.authenticate(promptInfo)
    }

    LaunchedEffect(Unit) { startPrompt() }

    Box(modifier = Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
        Column(horizontalAlignment = Alignment.CenterHorizontally) {
            Icon(Icons.Filled.Lock, contentDescription = null, modifier = Modifier.size(48.dp))
            Spacer(Modifier.height(12.dp))
            Text("ValveBox قفل است")
            if (status.isNotBlank()) {
                Spacer(Modifier.height(8.dp))
                Text(status, style = MaterialTheme.typography.bodySmall)
            }
            if (canRetry) {
                Spacer(Modifier.height(16.dp))
                Button(onClick = { canRetry = false; startPrompt() }) {
                    Text("تلاش دوباره")
                }
            }
        }
    }
}
