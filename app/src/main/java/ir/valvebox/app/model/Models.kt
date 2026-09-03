package ir.valvebox.app.model

import org.json.JSONObject

data class DeviceInfo(
    val deviceId: String,
    val name: String,
    val zone: String,
    val status: String,   // "online" | "offline"
    val publicUrl: String,
    val ip: String
)

fun parseDevicesResponse(json: String): List<DeviceInfo> {
    val root = JSONObject(json)
    val arr = root.optJSONArray("devices") ?: return emptyList()
    val list = mutableListOf<DeviceInfo>()
    for (i in 0 until arr.length()) {
        val o = arr.getJSONObject(i)
        list.add(
            DeviceInfo(
                deviceId = o.optString("device_id"),
                name = o.optString("name"),
                zone = o.optString("zone"),
                status = o.optString("status"),
                publicUrl = o.optString("public_url"),
                ip = o.optString("ip")
            )
        )
    }
    return list
}

data class GasGauge(
    val gas: String,
    val value: Double,
    val unit: String,
    val alarm: String // ok | low | high | fault
)

data class DeviceLiveData(
    val status: String,
    val lastSeen: String?,
    val gauges: List<GasGauge>
)

private val GASES = listOf("O2", "N2O", "AIR", "CO2", "VAC")

fun parseDeviceDataResponse(json: String): DeviceLiveData {
    val root = JSONObject(json)
    val status = root.optString("status", "offline")
    val lastSeen = if (root.isNull("last_seen")) null else root.optString("last_seen")
    val payload = root.optJSONObject("payload")
    val gaugesObj = payload?.optJSONObject("gauges")

    val gauges = GASES.map { gasName ->
        val g = gaugesObj?.optJSONObject(gasName)
        GasGauge(
            gas = gasName,
            value = g?.optDouble("value", 0.0) ?: 0.0,
            unit = g?.optString("unit", "") ?: "",
            alarm = (g?.optString("alarm", "ok") ?: "ok").lowercase()
        )
    }
    return DeviceLiveData(status = status, lastSeen = lastSeen, gauges = gauges)
}

data class HistoryEntry(
    val gas: String,
    val startTime: String,
    val endTime: String?,
    val resolvedBy: String?,
    val durationSec: Int?
)

fun parseHistoryResponse(json: String): List<HistoryEntry> {
    val root = JSONObject(json)
    val arr = root.optJSONArray("events") ?: return emptyList()
    val list = mutableListOf<HistoryEntry>()
    for (i in 0 until arr.length()) {
        val o = arr.getJSONObject(i)
        list.add(
            HistoryEntry(
                gas = o.optString("gas_type"),
                startTime = o.optString("start_time"),
                endTime = if (o.isNull("end_time")) null else o.optString("end_time"),
                resolvedBy = if (o.isNull("resolved_by")) null else o.optString("resolved_by"),
                durationSec = if (o.isNull("duration_sec")) null else o.optInt("duration_sec")
            )
        )
    }
    return list
}
