package ir.valvebox.app.ui

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.grid.GridCells
import androidx.compose.foundation.lazy.grid.LazyVerticalGrid
import androidx.compose.foundation.lazy.grid.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import ir.valvebox.app.model.DeviceInfo
import ir.valvebox.app.model.GasGauge
import ir.valvebox.app.model.parseDeviceDataResponse
import ir.valvebox.app.network.ApiClient
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.withContext

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun DeviceScreen(
    device: DeviceInfo,
    onBack: () -> Unit,
    onOpenFullControl: (String) -> Unit,
    onOpenHistory: () -> Unit,
    onOpenTrends: () -> Unit
) {
    var statusText by remember { mutableStateOf("در حال اتصال…") }
    var offline by remember { mutableStateOf(false) }
    var gauges by remember { mutableStateOf<List<GasGauge>>(emptyList()) }

    LaunchedEffect(device.deviceId) {
        while (true) {
            val result = withContext(Dispatchers.IO) { ApiClient.fetchDeviceData(device.deviceId) }
            result.onSuccess { json ->
                val data = parseDeviceDataResponse(json)
                offline = data.status == "offline"
                statusText = "وضعیت: ${data.status} — آخرین اتصال: ${data.lastSeen ?: "—"}"
                gauges = data.gauges
            }.onFailure {
                statusText = "اتصال به هاب قطع شد — تلاش مجدد…"
            }
            delay(3000)
        }
    }

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text(device.name) },
                navigationIcon = {
                    IconButton(onClick = onBack) {
                        Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = "بازگشت")
                    }
                },
                actions = {
                    TextButton(onClick = onOpenTrends) {
                        Text("روند")
                    }
                    TextButton(onClick = onOpenHistory) {
                        Text("تاریخچه")
                    }
                    if (device.publicUrl.isNotBlank()) {
                        TextButton(onClick = { onOpenFullControl("${device.publicUrl}/settings") }) {
                            Text("کنترل کامل")
                        }
                    }
                }
            )
        }
    ) { padding ->
        Column(modifier = Modifier.padding(padding).fillMaxSize()) {
            if (offline) {
                Box(
                    modifier = Modifier.fillMaxWidth()
                        .background(Color(0xFF3A1414))
                        .padding(10.dp),
                    contentAlignment = Alignment.Center
                ) {
                    Text("این دستگاه آفلاین است — آخرین مقادیر شناخته‌شده نمایش داده می‌شود", color = Color(0xFFFCA5A5))
                }
            }
            Text(
                statusText,
                style = MaterialTheme.typography.bodySmall,
                modifier = Modifier.fillMaxWidth().padding(8.dp),
                textAlign = androidx.compose.ui.text.style.TextAlign.Center
            )

            LazyVerticalGrid(
                columns = GridCells.Fixed(2),
                contentPadding = PaddingValues(14.dp),
                horizontalArrangement = Arrangement.spacedBy(14.dp),
                verticalArrangement = Arrangement.spacedBy(14.dp)
            ) {
                items(gauges) { g -> GasCard(g) }
            }
        }
    }
}

/**
 * کارت گیج به شکل مربع، با نمایش دیجیتال (فونت مونواسپیس مثل ساعت دیجیتال)،
 * اسم گاز بالای عدد، و رنگ متن‌ها ثابت و روشن روی پس‌زمینه‌ی تیره تا همیشه
 * خوانا باشد (مستقل از تم دستگاه).
 */
@Composable
private fun GasCard(g: GasGauge) {
    val (accentColor, bgColor) = when (g.alarm) {
        "fault" -> Color(0xFFA855F7) to Color(0xFF241033)
        "low" -> Color(0xFFF59E0B) to Color(0xFF332305)
        "high" -> Color(0xFFEF4444) to Color(0xFF330D0D)
        else -> Color(0xFF22C55E) to Color(0xFF0B1220)
    }
    val textColor = Color(0xFFF1F5F9)   // تقریباً سفید، روی هر پس‌زمینه‌ی تیره خوانا است
    val mutedColor = Color(0xFFB6C2CF)  // خاکستری روشن برای متن‌های فرعی (واحد و وضعیت)

    Card(
        shape = RoundedCornerShape(18.dp),
        colors = CardDefaults.cardColors(containerColor = bgColor),
        border = BorderStroke(2.dp, accentColor),
        modifier = Modifier
            .fillMaxWidth()
            .aspectRatio(1f) // کارت کاملاً مربعی
    ) {
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(14.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
            verticalArrangement = Arrangement.Center
        ) {
            // اسم گاز — همیشه بالای کارت
            Text(
                text = g.gas,
                color = textColor,
                fontWeight = FontWeight.Bold,
                style = MaterialTheme.typography.titleMedium
            )

            Spacer(Modifier.weight(1f))

            // عدد به‌صورت دیجیتال (مونواسپیس، بزرگ)
            Text(
                text = if (g.alarm == "fault") "FAULT" else String.format("%.2f", g.value),
                color = textColor,
                fontFamily = FontFamily.Monospace,
                fontWeight = FontWeight.Bold,
                style = MaterialTheme.typography.displaySmall
            )

            if (g.alarm != "fault" && g.unit.isNotBlank()) {
                Text(
                    text = g.unit,
                    color = mutedColor,
                    style = MaterialTheme.typography.bodySmall
                )
            }

            Spacer(Modifier.weight(1f))

            Text(
                text = g.alarm.uppercase(),
                color = accentColor,
                fontWeight = FontWeight.Bold,
                style = MaterialTheme.typography.labelMedium
            )
        }
    }
}
