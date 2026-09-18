package com.cargoresq.driver.api

import com.cargoresq.driver.model.*
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.HttpUrl
import okhttp3.HttpUrl.Companion.toHttpUrlOrNull
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONArray
import org.json.JSONObject
import java.io.IOException
import java.util.concurrent.TimeUnit

/**
 * The API client.
 *
 * Every call returns a real [Result]. A failure is a failure.
 *
 * The previous implementation returned `Result.success(...)` carrying a
 * fabricated "Rajesh Kumar" session on *both* a non-2xx response and a thrown
 * exception, so a wrong password, a 500, and no server at all were
 * indistinguishable from a successful login. The SOS call returned
 * "BREAKDOWN_REPORTED" whether or not the request ever left the device.
 *
 * An app that cannot tell you it failed is worse than one that crashes,
 * because the driver stands by the road believing help is coming.
 */
object CargoResQApi {

    private val JSON = "application/json; charset=utf-8".toMediaType()
    private val FORM = "application/x-www-form-urlencoded".toMediaType()

    fun sanitizeBaseUrl(raw: String): String {
        var s = raw.trim()
        if (s.isEmpty()) return "http://10.0.2.2:8000"
        if (!s.startsWith("http://", ignoreCase = true) && !s.startsWith("https://", ignoreCase = true)) {
            s = "http://$s"
        }
        return s.trimEnd('/')
    }

    @Volatile
    var baseUrl: String = "http://10.0.2.2:8000"
        set(value) {
            field = sanitizeBaseUrl(value)
        }

    private val client: OkHttpClient by lazy {
        OkHttpClient.Builder()
            .connectTimeout(8, TimeUnit.SECONDS)
            .readTimeout(15, TimeUnit.SECONDS)
            .writeTimeout(15, TimeUnit.SECONDS)
            .retryOnConnectionFailure(true)
            .build()
    }

    /** A failure the UI can explain to the driver. */
    class ApiException(
        message: String,
        val statusCode: Int? = null,
        val isNetwork: Boolean = false,
    ) : Exception(message)

    // -- plumbing ---------------------------------------------------------

    private fun buildUrl(path: String): HttpUrl {
        val cleanBase = sanitizeBaseUrl(baseUrl)
        val cleanPath = if (path.startsWith("/")) path else "/$path"
        val full = cleanBase + cleanPath
        return full.toHttpUrlOrNull()
            ?: ("http://127.0.0.1:8000" + cleanPath).toHttpUrlOrNull()
            ?: ("http://10.0.2.2:8000" + cleanPath).toHttpUrlOrNull()!!
    }

    private suspend fun execute(requestSupplier: () -> Request): Result<String> = withContext(Dispatchers.IO) {
        try {
            val request = requestSupplier()
            client.newCall(request).execute().use { response ->
                val body = response.body?.string().orEmpty()
                if (response.isSuccessful) {
                    Result.success(body)
                } else {
                    Result.failure(
                        ApiException(describeError(response.code, body), response.code)
                    )
                }
            }
        } catch (e: IOException) {
            Result.failure(
                ApiException(
                    "Cannot reach CargoResQ at " + baseUrl +
                        ". Check your connection and the server address.",
                    isNetwork = true,
                )
            )
        } catch (e: IllegalArgumentException) {
            Result.failure(ApiException(e.message ?: "Invalid server URL"))
        } catch (e: Exception) {
            Result.failure(ApiException(e.message ?: "Unexpected error"))
        }
    }

    private suspend fun execute(request: Request): Result<String> = execute { request }

    /** Turn an error response into something a driver can act on. */
    private fun describeError(code: Int, body: String): String {
        val detail = runCatching {
            val json = JSONObject(body)
            when (val d = json.opt("detail")) {
                is JSONObject -> d.optString("message", d.toString())
                is String -> d
                else -> null
            }
        }.getOrNull()

        return when (code) {
            401 -> detail ?: "Incorrect email or password."
            403 -> detail ?: "Your account is not allowed to do that."
            404 -> detail ?: "Not found."
            409 -> detail ?: "Someone else has already handled that."
            422 -> detail ?: "The server rejected those details."
            in 500..599 -> "The server had a problem (HTTP $code). Try again shortly."
            else -> detail ?: "Request failed (HTTP $code)."
        }
    }

    private fun get(path: String, token: String?): Request =
        Request.Builder().url(buildUrl(path)).apply {
            token?.let { header("Authorization", "Bearer $it") }
        }.get().build()

    private fun post(path: String, token: String?, body: JSONObject?): Request =
        Request.Builder().url(buildUrl(path)).apply {
            token?.let { header("Authorization", "Bearer $it") }
        }.post((body?.toString() ?: "{}").toRequestBody(JSON)).build()

    private fun formPost(path: String, form: String): Request =
        Request.Builder().url(buildUrl(path)).post(form.toRequestBody(FORM)).build()

    // -- auth -------------------------------------------------------------

    suspend fun login(email: String, password: String): Result<DriverSession> = runCatching {
        val form = "username=" + enc(email) + "&password=" + enc(password)
        formPost("/api/v1/driver/login", form)
    }.fold(
        onSuccess = { req ->
            execute(req).mapCatching { body ->
                val json = JSONObject(body)
                DriverSession(
                    token = json.getString("access_token"),
                    driverId = json.getString("driver_id"),
                    driverName = json.getString("driver_name"),
                    companyId = json.getString("company_id"),
                    email = email,
                )
            }
        },
        onFailure = { err ->
            Result.failure(ApiException(err.message ?: "Invalid server address"))
        }
    )

    suspend fun profile(token: String): Result<DriverProfile> =
        execute(get("/api/v1/driver/me", token)).mapCatching { body ->
            val json = JSONObject(body)
            val truckJson = json.optJSONObject("truck")
            DriverProfile(
                id = json.getString("id"),
                name = json.getString("name"),
                email = json.getString("email"),
                phone = json.optStringOrNull("phone"),
                companyId = json.getString("companyId"),
                assignedTruckId = json.optStringOrNull("assignedTruckId"),
                truck = truckJson?.let {
                    TruckInfo(
                        id = it.getString("id"),
                        registrationNumber = it.getString("registrationNumber"),
                        latitude = it.optDoubleOrNull("latitude"),
                        longitude = it.optDoubleOrNull("longitude"),
                        status = it.optString("status", "unknown"),
                        refrigerated = it.optBoolean("refrigerated", false),
                        minTempC = it.optDoubleOrNull("minTempC"),
                    )
                },
            )
        }

    // -- assignment -------------------------------------------------------

    suspend fun activeShipment(token: String): Result<ShipmentInfo?> =
        execute(get("/api/v1/driver/active-shipment", token)).mapCatching { body ->
            val shipment = JSONObject(body).optJSONObject("shipment")
            if (shipment == null) null else ShipmentInfo(
                id = shipment.getString("id"),
                cargoType = shipment.getString("cargoType"),
                status = shipment.optString("status", "in_transit"),
                requiresRefrigeration = shipment.optBoolean("requiresRefrigeration", false),
                requiredMaxTempC = shipment.optDoubleOrNull("requiredMaxTempC"),
                isHazmat = shipment.optBoolean("isHazmat", false),
                volumeM3 = shipment.optDouble("volumeM3", 0.0),
                weightKg = shipment.optDouble("weightKg", 0.0),
                valueInr = shipment.optDouble("valueInr", 0.0),
            )
        }

    suspend fun activeIncident(token: String): Result<IncidentInfo?> =
        execute(get("/api/v1/driver/active-incident", token)).mapCatching { body ->
            val incident = JSONObject(body).optJSONObject("incident")
            if (incident == null) null else IncidentInfo(
                id = incident.getString("id"),
                shipmentId = incident.optString("shipmentId"),
                cargoType = incident.optStringOrNull("cargoType"),
                state = incident.optString("state", "UNKNOWN"),
                minutesUntilSpoilage = incident.optDoubleOrNull("minutesUntilSpoilage"),
            )
        }

    suspend fun reportBreakdown(
        token: String,
        latitude: Double,
        longitude: Double,
        hoursToSpoilage: Double?,
        clientRequestId: String,
    ): Result<String> {
        val payload = JSONObject().apply {
            put("lat", latitude)
            put("lng", longitude)
            hoursToSpoilage?.let { put("hours_to_spoilage", it) }
            put("client_request_id", clientRequestId)
        }
        return execute(post("/api/v1/driver/report-breakdown", token, payload))
            .mapCatching { JSONObject(it).optString("status", "breakdown_registered") }
    }

    // -- offers -----------------------------------------------------------

    suspend fun offerInbox(token: String): Result<List<OfferInfo>> =
        execute(get("/api/v1/offers/inbox", token)).mapCatching { body ->
            JSONArray(body).mapObjects { json ->
                OfferInfo(
                    id = json.getString("id"),
                    incidentId = json.optString("incidentId"),
                    state = json.optString("state"),
                    etaMinutes = json.optDoubleOrNull("etaMinutes"),
                    distanceKm = json.optDoubleOrNull("distanceKm"),
                    expiresAt = json.optStringOrNull("expiresAt"),
                    carrierAccepted = json.optBoolean("carrierAccepted", false),
                    ownerConfirmed = json.optBoolean("ownerConfirmed", false),
                    bound = json.optBoolean("bound", false),
                )
            }
        }

    suspend fun acceptOffer(token: String, offerId: String): Result<Boolean> =
        execute(post("/api/v1/offers/$offerId/carrier-accept", token, null))
            .mapCatching { JSONObject(it).optBoolean("bound", false) }

    suspend fun declineOffer(token: String, offerId: String, reason: String?): Result<Unit> =
        execute(
            post(
                "/api/v1/offers/$offerId/carrier-decline",
                token,
                JSONObject().apply { reason?.let { put("reason", it) } },
            )
        ).map { }

    // -- telemetry --------------------------------------------------------

    suspend fun uploadPings(
        token: String,
        deviceId: String,
        pings: List<QueuedPing>,
    ): Result<Int> {
        val array = JSONArray()
        pings.forEach { ping ->
            array.put(JSONObject().apply {
                put("client_ping_id", ping.clientPingId)
                put("recorded_at", ping.recordedAt)
                put("latitude", ping.latitude)
                put("longitude", ping.longitude)
                ping.accuracyM?.let { put("accuracy_m", it.toDouble()) }
                ping.speedKph?.let { put("speed_kph", it) }
                ping.headingDeg?.let { put("heading_deg", it) }
                ping.batteryPct?.let { put("battery_pct", it) }
                ping.isCharging?.let { put("is_charging", it) }
                put("mock_location", ping.mockLocation)
            })
        }
        val payload = JSONObject().apply {
            put("device_id", deviceId)
            put("pings", array)
        }
        return execute(post("/api/v1/telemetry/pings", token, payload))
            .mapCatching { JSONObject(it).optInt("accepted", 0) }
    }

    suspend fun uploadReading(
        token: String,
        sensorId: String,
        shipmentId: String,
        recordedAt: String,
        temperatureC: Double?,
        doorOpen: Boolean?,
        reeferState: String?,
        clientReadingId: String,
    ): Result<Int> {
        val reading = JSONObject().apply {
            put("client_reading_id", clientReadingId)
            put("shipment_id", shipmentId)
            put("recorded_at", recordedAt)
            temperatureC?.let { put("temperature_c", it) }
            doorOpen?.let { put("door_open", it) }
            reeferState?.let { put("reefer_state", it) }
            // Honest about provenance: a driver reading a gauge is not a
            // signed telematics feed, and settlement weighs them differently.
            put("source", "device")
        }
        val payload = JSONObject().apply {
            put("sensor_id", sensorId)
            put("readings", JSONArray().put(reading))
        }
        return execute(post("/api/v1/telemetry/readings", token, payload))
            .mapCatching { JSONObject(it).optInt("accepted", 0) }
    }

    suspend fun telemetryConfig(token: String): Result<TelemetryConfig> =
        execute(get("/api/v1/telemetry/config", token)).mapCatching { body ->
            val json = JSONObject(body)
            TelemetryConfig(
                pingIntervalSeconds = json.optInt("pingIntervalSeconds", 30),
                pingIntervalWhileStationarySeconds =
                    json.optInt("pingIntervalWhileStationarySeconds", 300),
                batchSize = json.optInt("batchSize", 50),
                queueLimit = json.optInt("queueLimit", 2000),
            )
        }

    // -- SOS --------------------------------------------------------------

    suspend fun raiseSos(
        token: String,
        category: SosCategory,
        severity: SosSeverity,
        latitude: Double,
        longitude: Double,
        accuracyM: Float?,
        landmarkNote: String?,
        condition: DriverCondition?,
        silentMode: Boolean,
        clientRequestId: String,
    ): Result<SosAlertInfo> {
        val payload = JSONObject().apply {
            put("category", category.wire)
            put("severity", severity.wire)
            put("latitude", latitude)
            put("longitude", longitude)
            accuracyM?.let { put("accuracy_m", it.toDouble()) }
            landmarkNote?.takeIf { it.isNotBlank() }?.let { put("landmark_note", it) }
            put("silent_mode", silentMode)
            put("client_request_id", clientRequestId)
            condition?.let { c ->
                put("condition", JSONObject().apply {
                    c.personsAffected?.let { put("persons_affected", it) }
                    c.isConscious?.let { put("is_conscious", it) }
                    c.isBreathing?.let { put("is_breathing", it) }
                    c.isTrapped?.let { put("is_trapped", it) }
                    c.isMobile?.let { put("is_mobile", it) }
                    c.severeBleeding?.let { put("severe_bleeding", it) }
                    c.note?.takeIf { n -> n.isNotBlank() }?.let { put("note", it) }
                })
            }
        }
        return execute(post("/api/v1/sos", token, payload)).mapCatching { body ->
            val json = JSONObject(body)
            SosAlertInfo(
                id = json.getString("id"),
                category = json.optString("category"),
                severity = json.optString("severity"),
                status = json.optString("status"),
                // Surfaced so no screen can imply the emergency services were
                // contacted. They were not.
                psapDispatched = json.optBoolean("psapDispatched", false),
                emergencyNumbers = json.optJSONArray("emergencyNumbers").toEmergencyNumbers(),
            )
        }
    }

    suspend fun emergencyNumbers(): Result<List<EmergencyNumber>> =
        execute(get("/api/v1/sos/emergency-numbers", null)).mapCatching { body ->
            JSONObject(body).optJSONArray("numbers").toEmergencyNumbers()
        }

    suspend fun cancelSos(token: String, sosId: String, pin: String): Result<Unit> =
        execute(
            post(
                "/api/v1/sos/$sosId/cancel",
                token,
                JSONObject().apply { put("pin", pin) },
            )
        ).map { }

    suspend fun emergencyContacts(token: String): Result<List<EmergencyContact>> =
        execute(get("/api/v1/emergency-contacts", token)).mapCatching { body ->
            JSONArray(body).mapObjects { json ->
                EmergencyContact(
                    id = json.getString("id"),
                    name = json.getString("name"),
                    phone = json.getString("phone"),
                    relationship = json.optStringOrNull("relationship"),
                    isPrimary = json.optBoolean("isPrimary", false),
                )
            }
        }

    suspend fun addEmergencyContact(
        token: String,
        name: String,
        phone: String,
        relationship: String?,
    ): Result<Unit> =
        execute(
            post(
                "/api/v1/emergency-contacts",
                token,
                JSONObject().apply {
                    put("name", name)
                    put("phone", phone)
                    relationship?.takeIf { it.isNotBlank() }?.let { put("relationship", it) }
                },
            )
        ).map { }

    // -- helpers ----------------------------------------------------------

    private fun enc(value: String): String =
        java.net.URLEncoder.encode(value, "UTF-8")

    private fun JSONObject.optStringOrNull(key: String): String? =
        if (isNull(key)) null else optString(key).takeIf { it.isNotEmpty() }

    private fun JSONObject.optDoubleOrNull(key: String): Double? =
        if (isNull(key)) null else optDouble(key).takeIf { !it.isNaN() }

    private fun <T> JSONArray.mapObjects(transform: (JSONObject) -> T): List<T> {
        val out = ArrayList<T>(length())
        for (i in 0 until length()) {
            optJSONObject(i)?.let { out.add(transform(it)) }
        }
        return out
    }

    private fun JSONArray?.toEmergencyNumbers(): List<EmergencyNumber> {
        if (this == null) return emptyList()
        val out = ArrayList<EmergencyNumber>(length())
        for (i in 0 until length()) {
            optJSONObject(i)?.let { json ->
                out.add(
                    EmergencyNumber(
                        label = json.optString("label"),
                        number = json.optString("number"),
                        primary = json.optBoolean("primary", false),
                    )
                )
            }
        }
        return out
    }
}
