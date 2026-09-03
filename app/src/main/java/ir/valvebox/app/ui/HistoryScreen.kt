package ir.valvebox.app.ui

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import ir.valvebox.app.model.HistoryEntry
import ir.valvebox.app.model.parseHistoryResponse
import ir.valvebox.app.network.ApiClient
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun HistoryScreen(deviceId: String, deviceName: String, onBack: () -> Unit) {
    var entries by remember { mutableStateOf<List<HistoryEntry>>(emptyList()) }
    var loading by remember { mutableStateOf(true) }
    var error by remember { mutableStateOf<String?>(null) }

    LaunchedEffect(deviceId) {
        val result = withContext(Dispatchers.IO) { ApiClient.fetchDeviceHistory(deviceId) }
        result.onSuccess {
            entries = parseHistoryResponse(it)
            error = null
        }.onFailure {
            error = it.message
        }
        loading = false
    }

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text("تاریخچه — $deviceName") },
                navigationIcon = {
                    IconButton(onClick = onBack) {
                        Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = "بازگشت")
                    }
                }
            )
        }
    ) { padding ->
        Box(modifier = Modifier.padding(padding).fillMaxSize()) {
            when {
                loading -> CircularProgressIndicator(modifier = Modifier.align(Alignment.Center))
                error != null -> Text(
                    "خطا در دریافت تاریخچه: $error",
                    modifier = Modifier.align(Alignment.Center).padding(16.dp),
                    color = MaterialTheme.colorScheme.error
                )
                entries.isEmpty() -> Text(
                    "هیچ رویدادی ثبت نشده.",
                    modifier = Modifier.align(Alignment.Center)
                )
                else -> LazyColumn(
                    contentPadding = PaddingValues(12.dp),
                    verticalArrangement = Arrangement.spacedBy(10.dp)
                ) {
                    items(entries) { e -> HistoryRow(e) }
                }
            }
        }
    }
}

@Composable
private fun HistoryRow(e: HistoryEntry) {
    Card(shape = RoundedCornerShape(14.dp), modifier = Modifier.fillMaxWidth()) {
        Column(modifier = Modifier.padding(14.dp)) {
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween
            ) {
                Text(e.gas, fontWeight = FontWeight.Bold)
                Text(
                    if (e.endTime == null) "برطرف‌نشده" else "برطرف‌شده",
                    color = if (e.endTime == null) MaterialTheme.colorScheme.error else Color(0xFF16A34A),
                    fontWeight = FontWeight.Bold,
                    style = MaterialTheme.typography.labelMedium
                )
            }
            Spacer(Modifier.height(6.dp))
            Text("شروع: ${e.startTime}", style = MaterialTheme.typography.bodySmall)
            if (e.endTime != null) {
                Text("پایان: ${e.endTime}", style = MaterialTheme.typography.bodySmall)
            }
            if (e.resolvedBy != null) {
                Text("رفع‌شده توسط: ${e.resolvedBy}", style = MaterialTheme.typography.bodySmall)
            }
            if (e.durationSec != null) {
                val minutes = e.durationSec / 60
                Text("مدت: $minutes دقیقه", style = MaterialTheme.typography.bodySmall)
            }
        }
    }
}
