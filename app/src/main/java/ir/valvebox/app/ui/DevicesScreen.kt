package ir.valvebox.app.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.grid.GridCells
import androidx.compose.foundation.lazy.grid.LazyVerticalGrid
import androidx.compose.foundation.lazy.grid.items
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.Logout
import androidx.compose.material.icons.filled.AdminPanelSettings
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import com.google.firebase.messaging.FirebaseMessaging
import ir.valvebox.app.model.DeviceInfo
import ir.valvebox.app.model.parseDevicesResponse
import ir.valvebox.app.network.ApiClient
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.tasks.await
import kotlinx.coroutines.withContext

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun DevicesScreen(
    onOpenDevice: (DeviceInfo) -> Unit,
    onLogout: () -> Unit,
    onOpenAdminPanel: () -> Unit
) {
    var devices by remember { mutableStateOf<List<DeviceInfo>>(emptyList()) }
    var error by remember { mutableStateOf<String?>(null) }
    var loading by remember { mutableStateOf(true) }
    val scope = rememberCoroutineScope()
    val context = LocalContext.current
    val isAdmin = remember { ApiClient.isAdmin(context) }

    suspend fun refresh() {
        val result = withContext(Dispatchers.IO) { ApiClient.fetchDevices() }
        result.onSuccess {
            devices = parseDevicesResponse(it)
            error = null
        }.onFailure {
            error = it.message
        }
        loading = false
    }

    // ثبت توکن پوش فایربیس (فقط اگر Firebase در پروژه راه‌اندازی شده باشد؛
    // در غیر این صورت این تلاش بی‌صدا نادیده گرفته می‌شود و اپ طبیعی کار می‌کند)
    LaunchedEffect(Unit) {
        try {
            val token = FirebaseMessaging.getInstance().token.await()
            withContext(Dispatchers.IO) { ApiClient.registerPushToken(token) }
        } catch (e: Exception) {
            // Firebase راه‌اندازی نشده یا شبکه در دسترس نیست — مشکلی نیست
        }
    }

    LaunchedEffect(Unit) {
        while (true) {
            refresh()
            delay(5000)
        }
    }

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text("دستگاه‌ها") },
                actions = {
                    if (isAdmin) {
                        IconButton(onClick = onOpenAdminPanel) {
                            Icon(Icons.Filled.AdminPanelSettings, contentDescription = "پنل مدیریت")
                        }
                    }
                    IconButton(onClick = {
                        scope.launch(Dispatchers.IO) { ApiClient.logout() }
                        onLogout()
                    }) {
                        Icon(Icons.AutoMirrored.Filled.Logout, contentDescription = "خروج")
                    }
                }
            )
        }
    ) { padding ->
        Box(modifier = Modifier.padding(padding).fillMaxSize()) {
            when {
                loading -> CircularProgressIndicator(modifier = Modifier.align(Alignment.Center))
                error != null -> Text(
                    "خطا: $error",
                    modifier = Modifier.align(Alignment.Center).padding(16.dp),
                    color = MaterialTheme.colorScheme.error
                )
                devices.isEmpty() -> Text(
                    "هیچ دستگاهی برای این حساب تعریف نشده.",
                    modifier = Modifier.align(Alignment.Center).padding(16.dp)
                )
                else -> LazyVerticalGrid(
                    columns = GridCells.Fixed(2),
                    contentPadding = PaddingValues(12.dp),
                    horizontalArrangement = Arrangement.spacedBy(12.dp),
                    verticalArrangement = Arrangement.spacedBy(12.dp)
                ) {
                    items(devices) { dev ->
                        DeviceCard(dev, onClick = { onOpenDevice(dev) })
                    }
                }
            }
        }
    }
}

@Composable
private fun DeviceCard(dev: DeviceInfo, onClick: () -> Unit) {
    val dotColor = if (dev.status == "online") Color(0xFF16A34A) else Color(0xFF6B7280)
    Card(
        shape = RoundedCornerShape(16.dp),
        modifier = Modifier.fillMaxWidth().clickable { onClick() }
    ) {
        Column(
            modifier = Modifier.padding(16.dp).fillMaxWidth(),
            horizontalAlignment = Alignment.CenterHorizontally
        ) {
            Box(
                modifier = Modifier.size(12.dp).background(dotColor, CircleShape)
            )
            Spacer(Modifier.height(8.dp))
            Text(dev.name, style = MaterialTheme.typography.titleMedium)
            if (dev.zone.isNotBlank()) {
                Text(dev.zone, style = MaterialTheme.typography.bodySmall)
            }
            Spacer(Modifier.height(4.dp))
            Text(dev.status.uppercase(), style = MaterialTheme.typography.labelSmall)
        }
    }
}
