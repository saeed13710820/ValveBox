package ir.valvebox.app.ui

import android.annotation.SuppressLint
import android.view.ViewGroup
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material3.*
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.viewinterop.AndroidView
import ir.valvebox.app.network.ApiClient

/**
 * صفحه‌ی وب توکار — هم برای «کنترل کامل» هر دستگاه استفاده می‌شود، هم برای
 * «پنل مدیریت» هاب. وقتی syncSessionCookie=true باشد (فقط برای پنل مدیریت
 * لازم است)، قبل از باز کردن صفحه، کوکی نشستِ لاگین‌شده‌ی اپ به WebView کپی
 * می‌شود تا کاربر مجبور به ورود دوباره نشود.
 */
@OptIn(ExperimentalMaterial3Api::class)
@SuppressLint("SetJavaScriptEnabled")
@Composable
fun FullControlScreen(
    url: String,
    onBack: () -> Unit,
    title: String = "کنترل کامل",
    syncSessionCookie: Boolean = false
) {
    val context = LocalContext.current

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text(title) },
                navigationIcon = {
                    IconButton(onClick = onBack) {
                        Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = "بازگشت")
                    }
                }
            )
        }
    ) { padding ->
        AndroidView(
            modifier = Modifier
                .padding(padding)
                .fillMaxSize(), // <-- مهم: بدون این خط، WebView کوچک و جمع می‌شود
            factory = { ctx ->
                if (syncSessionCookie) {
                    ApiClient.syncCookiesToWebView(ctx)
                }
                WebView(ctx).apply {
                    layoutParams = ViewGroup.LayoutParams(
                        ViewGroup.LayoutParams.MATCH_PARENT,
                        ViewGroup.LayoutParams.MATCH_PARENT
                    )
                    settings.javaScriptEnabled = true
                    settings.domStorageEnabled = true
                    settings.useWideViewPort = true
                    settings.loadWithOverviewMode = true
                    settings.builtInZoomControls = false
                    settings.setSupportZoom(false)
                    android.webkit.CookieManager.getInstance().setAcceptThirdPartyCookies(this, true)
                    webViewClient = WebViewClient()
                    loadUrl(url)
                }
            }
        )
    }
}
