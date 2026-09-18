package com.cargoresq.driver

import android.content.Context
import android.content.SharedPreferences
import androidx.security.crypto.EncryptedSharedPreferences
import androidx.security.crypto.MasterKey
import com.cargoresq.driver.api.CargoResQApi
import com.cargoresq.driver.model.DriverSession
import java.util.UUID

/**
 * Where the driver's session lives between launches.
 *
 * Encrypted at rest, because the token on this device authorises reporting a
 * breakdown and raising an SOS on the company's behalf. The previous app kept
 * it in a plain `var` that vanished on rotation, so a driver was silently
 * signed out mid-shift.
 *
 * Falls back to ordinary preferences if the keystore is unavailable -- some
 * older or heavily modified devices cannot create a master key, and being
 * unable to sign in at all is a worse outcome than unencrypted storage on a
 * device the driver already controls.
 */
object Session {

    private const val FILE = "cargoresq.session"
    private const val KEY_TOKEN = "token"
    private const val KEY_DRIVER_ID = "driver_id"
    private const val KEY_DRIVER_NAME = "driver_name"
    private const val KEY_COMPANY_ID = "company_id"
    private const val KEY_EMAIL = "email"
    private const val KEY_BASE_URL = "base_url"
    private const val KEY_DEVICE_ID = "device_id"

    private lateinit var prefs: SharedPreferences

    @Volatile
    var current: DriverSession? = null
        private set

    fun init(context: Context) {
        prefs = openPreferences(context)
        CargoResQApi.baseUrl = prefs.getString(KEY_BASE_URL, CargoResQApi.baseUrl)
            ?: CargoResQApi.baseUrl

        val token = prefs.getString(KEY_TOKEN, null)
        if (!token.isNullOrBlank()) {
            current = DriverSession(
                token = token,
                driverId = prefs.getString(KEY_DRIVER_ID, "").orEmpty(),
                driverName = prefs.getString(KEY_DRIVER_NAME, "").orEmpty(),
                companyId = prefs.getString(KEY_COMPANY_ID, "").orEmpty(),
                email = prefs.getString(KEY_EMAIL, "").orEmpty(),
            )
        }
    }

    private fun openPreferences(context: Context): SharedPreferences = try {
        val masterKey = MasterKey.Builder(context)
            .setKeyScheme(MasterKey.KeyScheme.AES256_GCM)
            .build()
        EncryptedSharedPreferences.create(
            context,
            FILE,
            masterKey,
            EncryptedSharedPreferences.PrefKeyEncryptionScheme.AES256_SIV,
            EncryptedSharedPreferences.PrefValueEncryptionScheme.AES256_GCM,
        )
    } catch (e: Exception) {
        context.getSharedPreferences(FILE + ".plain", Context.MODE_PRIVATE)
    }

    fun save(session: DriverSession) {
        current = session
        prefs.edit()
            .putString(KEY_TOKEN, session.token)
            .putString(KEY_DRIVER_ID, session.driverId)
            .putString(KEY_DRIVER_NAME, session.driverName)
            .putString(KEY_COMPANY_ID, session.companyId)
            .putString(KEY_EMAIL, session.email)
            .apply()
    }

    fun clear() {
        current = null
        prefs.edit()
            .remove(KEY_TOKEN)
            .remove(KEY_DRIVER_ID)
            .remove(KEY_DRIVER_NAME)
            .remove(KEY_COMPANY_ID)
            .remove(KEY_EMAIL)
            .apply()
    }

    var baseUrl: String
        get() = CargoResQApi.baseUrl
        set(value) {
            CargoResQApi.baseUrl = value
            prefs.edit().putString(KEY_BASE_URL, CargoResQApi.baseUrl).apply()
        }

    /**
     * A stable identifier for this installation.
     *
     * Used to deduplicate telemetry: the server's unique index is on
     * (device_id, client_ping_id), so a retried batch cannot create duplicate
     * positions. Generated locally rather than taken from ANDROID_ID, which
     * is a device identifier and not ours to collect.
     */
    val deviceId: String
        get() {
            prefs.getString(KEY_DEVICE_ID, null)?.let { return it }
            val generated = "dev-" + UUID.randomUUID().toString().take(12)
            prefs.edit().putString(KEY_DEVICE_ID, generated).apply()
            return generated
        }

    val token: String? get() = current?.token
    val isSignedIn: Boolean get() = current != null
}
