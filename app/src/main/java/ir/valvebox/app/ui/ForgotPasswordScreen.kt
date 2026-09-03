package ir.valvebox.app.ui

import androidx.compose.foundation.layout.*
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import ir.valvebox.app.network.ApiClient
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/**
 * صفحه‌ی داخلی اپ برای بازیابی رمز عبور — کاربر یوزرنیمش را وارد می‌کند
 * و بدون نیاز به خروج از اپ (بدون باز شدن مرورگر)، درخواست مستقیم به هاب
 * فرستاده می‌شود. اگر برای آن یوزرنیم ایمیلی ثبت شده باشد، لینک تنظیم رمز
 * جدید به ایمیلش می‌رود — باز کردن آن لینک طبیعتاً در مرورگر گوشی انجام
 * می‌شود (روال استاندارد در همه‌ی اپ‌ها، چون لینک از داخل ایمیل باز می‌شود).
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ForgotPasswordScreen(onBack: () -> Unit) {
    var username by remember { mutableStateOf("") }
    var loading by remember { mutableStateOf(false) }
    var resultMessage by remember { mutableStateOf<String?>(null) }
    var isError by remember { mutableStateOf(false) }
    val scope = rememberCoroutineScope()

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text("بازیابی رمز عبور") },
                navigationIcon = {
                    IconButton(onClick = onBack) {
                        Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = "بازگشت")
                    }
                }
            )
        }
    ) { padding ->
        Column(
            modifier = Modifier
                .padding(padding)
                .fillMaxSize()
                .padding(24.dp),
        ) {
            Text(
                "نام کاربری‌ات را وارد کن. اگر ایمیلی برایش ثبت شده باشد، لینک بازیابی رمز به آن ایمیل ارسال می‌شود.",
                style = MaterialTheme.typography.bodyMedium
            )
            Spacer(Modifier.height(20.dp))

            OutlinedTextField(
                value = username,
                onValueChange = { username = it },
                label = { Text("نام کاربری") },
                modifier = Modifier.fillMaxWidth(),
                enabled = !loading
            )
            Spacer(Modifier.height(16.dp))

            if (resultMessage != null) {
                Text(
                    resultMessage!!,
                    color = if (isError) MaterialTheme.colorScheme.error else MaterialTheme.colorScheme.primary
                )
                Spacer(Modifier.height(16.dp))
            }

            Button(
                onClick = {
                    resultMessage = null
                    loading = true
                    scope.launch {
                        val result = withContext(Dispatchers.IO) {
                            ApiClient.forgotPassword(username.trim())
                        }
                        loading = false
                        result.onSuccess {
                            isError = false
                            resultMessage = it
                        }.onFailure {
                            isError = true
                            resultMessage = it.message ?: "خطای نامشخص"
                        }
                    }
                },
                enabled = !loading && username.isNotBlank(),
                modifier = Modifier.fillMaxWidth()
            ) {
                if (loading) {
                    CircularProgressIndicator(modifier = Modifier.size(20.dp), strokeWidth = 2.dp)
                } else {
                    Text("ارسال لینک بازیابی")
                }
            }
        }
    }
}
