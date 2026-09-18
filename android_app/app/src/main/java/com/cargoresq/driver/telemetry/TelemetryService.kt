package com.cargoresq.driver.telemetry

import android.Manifest
import android.annotation.SuppressLint
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.content.pm.PackageManager
import android.content.pm.ServiceInfo
import android.location.Location
import android.location.LocationListener
import android.location.LocationManager
import android.os.BatteryManager
import android.os.Build
import android.os.IBinder
import android.os.Looper
import androidx.core.app.ActivityCompat
import androidx.core.app.NotificationCompat
import androidx.core.app.ServiceCompat
import com.cargoresq.driver.MainActivity
import com.cargoresq.driver.R
import com.cargoresq.driver.Session
import com.cargoresq.driver.api.CargoResQApi
import com.cargoresq.driver.model.QueuedPing
import com.google.android.gms.location.*
import kotlinx.coroutines.CoroutineExceptionHandler
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import java.text.SimpleDateFormat
import java.util.ArrayDeque
import java.util.Date
import java.util.Locale
import java.util.TimeZone
import java.util.UUID

/**
 * Keeps the company's map honest while the driver drives.
 *
 * A foreground service because position has to keep flowing with the screen
 * off -- that is the entire point of fleet telemetry, and Android will
 * otherwise freeze a backgrounded app within minutes. The persistent
 * notification is not an inconvenience to work around: it is the OS telling
 * the driver their location is being collected, which is exactly right.
 *
 * Fixes are queued and uploaded in batches, because a truck spends much of
 * its life without usable signal. The queue is bounded and drops its oldest
 * entries when full: arriving with six hours of stale positions is less
 * useful than arriving with the last hour, and unbounded growth would
 * eventually take the app down.
 *
 * Nothing here existed before. `ACCESS_FINE_LOCATION` was declared in the
 * manifest and not one line of code requested or read a location.
 */
class TelemetryService : Service() {

    companion object {
        const val ACTION_START = "com.cargoresq.driver.telemetry.START"
        const val ACTION_STOP = "com.cargoresq.driver.telemetry.STOP"

        private const val CHANNEL_ID = "cargoresq_telemetry"
        private const val NOTIFICATION_ID = 4201

        /** Cadence while moving, and while parked. */
        private const val MOVING_INTERVAL_MS = 5_000L
        private const val STATIONARY_INTERVAL_MS = 15_000L

        private const val UPLOAD_INTERVAL_MS = 5_000L
        private const val BATCH_SIZE = 50
        private const val QUEUE_LIMIT = 2_000

        private const val MOVING_SPEED_KPH = 5.0

        fun start(context: Context) {
            val intent = Intent(context, TelemetryService::class.java).apply {
                action = ACTION_START
            }
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                context.startForegroundService(intent)
            } else {
                context.startService(intent)
            }
        }

        fun stop(context: Context) {
            context.startService(
                Intent(context, TelemetryService::class.java).apply { action = ACTION_STOP }
            )
        }

        /** Positions already accepted by the server, for the UI to show. */
        @Volatile
        var lastUploadedAt: Long = 0L

        @Volatile
        var queueDepth: Int = 0

        @Volatile
        var lastError: String? = null

        @Volatile
        var lastLocation: Location? = null
    }

    /**
     * An exception in a launched coroutine reaches the thread's default
     * handler and kills the process. SupervisorJob stops one child failing
     * its siblings; it does nothing about an unhandled throw. The handler is
     * what keeps a bad upload from taking the app down mid-shift.
     */
    private val crashGuard = CoroutineExceptionHandler { _, throwable ->
        lastError = throwable.message ?: throwable::class.java.simpleName
    }

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO + crashGuard)
    private var uploadJob: Job? = null

    /** Whether startForeground() actually succeeded. */
    private var isForeground = false

    /** Bounded FIFO. Oldest fixes are dropped first when it overflows. */
    private val queue = ArrayDeque<QueuedPing>()

    private var fusedClient: FusedLocationProviderClient? = null
    private var fusedCallback: LocationCallback? = null
    private var locationManager: LocationManager? = null
    private var legacyListener: LocationListener? = null

    private val isoFormat = SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss.SSS'Z'", Locale.US).apply {
        timeZone = TimeZone.getTimeZone("UTC")
    }

    override fun onCreate() {
        super.onCreate()
        // Android can restart this service into a fresh process without ever
        // creating MainActivity, so the session has to be loaded here too.
        Session.init(applicationContext)
    }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == ACTION_STOP) {
            shutDown()
            return START_NOT_STICKY
        }

        // A null intent means Android restarted us after killing the process.
        // Only carry on if there is still something to report with.
        if (intent == null && (Session.token == null || !hasLocationPermission())) {
            shutDown()
            return START_NOT_STICKY
        }

        // Becoming foreground is not optional and not something we can retry
        // later. Once startForegroundService() has been called, Android gives
        // the service about five seconds to call startForeground(); if it does
        // not, the system throws RemoteServiceException and kills the whole
        // process -- from outside our code, so no try/catch can stop it. With
        // START_STICKY the service is then restarted straight back into the
        // same failure, which is what made the app appear to close itself
        // every few seconds.
        //
        // So: if we cannot go foreground, we stop cleanly instead.
        if (!enterForeground()) {
            shutDown()
            return START_NOT_STICKY
        }

        if (!hasLocationPermission()) {
            lastError = "Location permission not granted"
            shutDown()
            return START_NOT_STICKY
        }

        startTracking()
        return START_STICKY
    }

    /** Try to become a foreground service. Returns false if the system refused. */
    private fun enterForeground(): Boolean {
        val notification = try {
            buildNotification("Sharing position with dispatch")
        } catch (e: Exception) {
            lastError = "Could not build the duty notification: " + (e.message ?: "")
            return false
        }

        return try {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                ServiceCompat.startForeground(
                    this,
                    NOTIFICATION_ID,
                    notification,
                    ServiceInfo.FOREGROUND_SERVICE_TYPE_LOCATION,
                )
            } else {
                startForeground(NOTIFICATION_ID, notification)
            }
            isForeground = true
            true
        } catch (e: Exception) {
            // Android 12+ refuses a background start; 14+ additionally
            // refuses a location-typed service without the permission held.
            lastError = "Android refused the duty notification: " + (e.message ?: "")
            false
        }
    }

    /** Stop everything and leave the foreground state cleanly. */
    private fun shutDown() {
        stopTracking()
        if (isForeground) {
            runCatching {
                ServiceCompat.stopForeground(this, ServiceCompat.STOP_FOREGROUND_REMOVE)
            }
            isForeground = false
        }
        stopSelf()
    }

    override fun onDestroy() {
        stopTracking()
        scope.cancel()
        super.onDestroy()
    }

    // -- location ---------------------------------------------------------

    private fun hasLocationPermission(): Boolean =
        ActivityCompat.checkSelfPermission(this, Manifest.permission.ACCESS_FINE_LOCATION) ==
            PackageManager.PERMISSION_GRANTED ||
            ActivityCompat.checkSelfPermission(this, Manifest.permission.ACCESS_COARSE_LOCATION) ==
            PackageManager.PERMISSION_GRANTED

    private fun startTracking() {
        if (!startFusedUpdates()) {
            // Play Services is absent or unusable. LocationManager is always
            // there, so the app still works on a device without Google's
            // services rather than simply not reporting.
            startLegacyUpdates()
        }

        uploadJob?.cancel()
        uploadJob = scope.launch {
            while (true) {
                delay(UPLOAD_INTERVAL_MS)
                try {
                    flush()
                } catch (e: Exception) {
                    // One failed upload must not end the loop. The queue keeps
                    // the fixes and the next tick tries again.
                    lastError = e.message
                }
            }
        }
    }

    @SuppressLint("MissingPermission")
    private fun startFusedUpdates(): Boolean = try {
        val client = LocationServices.getFusedLocationProviderClient(this)
        val request = LocationRequest.Builder(
            Priority.PRIORITY_HIGH_ACCURACY,
            MOVING_INTERVAL_MS,
        )
            .setMinUpdateIntervalMillis(MOVING_INTERVAL_MS / 2)
            .setMaxUpdateDelayMillis(STATIONARY_INTERVAL_MS)
            .build()

        val callback = object : LocationCallback() {
            override fun onLocationResult(result: LocationResult) {
                result.lastLocation?.let { record(it) }
            }
        }
        client.requestLocationUpdates(request, callback, Looper.getMainLooper())
        client.lastLocation.addOnSuccessListener { loc ->
            if (loc != null) record(loc)
        }
        fusedClient = client
        fusedCallback = callback
        true
    } catch (e: Exception) {
        lastError = "Fused location unavailable: " + (e.message ?: "unknown")
        false
    }

    @SuppressLint("MissingPermission")
    private fun startLegacyUpdates() {
        try {
            val manager = getSystemService(Context.LOCATION_SERVICE) as LocationManager
            val listener = LocationListener { location -> record(location) }
            var registered = false

            if (manager.isProviderEnabled(LocationManager.GPS_PROVIDER)) {
                manager.requestLocationUpdates(
                    LocationManager.GPS_PROVIDER, MOVING_INTERVAL_MS, 0f, listener, Looper.getMainLooper()
                )
                manager.getLastKnownLocation(LocationManager.GPS_PROVIDER)?.let { record(it) }
                registered = true
            }
            if (manager.isProviderEnabled(LocationManager.NETWORK_PROVIDER)) {
                manager.requestLocationUpdates(
                    LocationManager.NETWORK_PROVIDER, MOVING_INTERVAL_MS, 0f, listener, Looper.getMainLooper()
                )
                manager.getLastKnownLocation(LocationManager.NETWORK_PROVIDER)?.let { record(it) }
                registered = true
            }

            if (!registered) {
                lastError = "Location is switched off on this device"
                return
            }
            locationManager = manager
            legacyListener = listener
        } catch (e: SecurityException) {
            lastError = "Location permission was revoked"
        } catch (e: Exception) {
            lastError = e.message
        }
    }

    private fun stopTracking() {
        fusedCallback?.let { callback -> fusedClient?.removeLocationUpdates(callback) }
        fusedCallback = null
        fusedClient = null
        legacyListener?.let { listener ->
            runCatching { locationManager?.removeUpdates(listener) }
        }
        legacyListener = null
        locationManager = null
        uploadJob?.cancel()
        uploadJob = null
    }

    // -- queueing ---------------------------------------------------------

    private fun record(location: Location) {
        lastLocation = location
        val speedKph = if (location.hasSpeed()) location.speed * 3.6 else null
        val ping = QueuedPing(
            clientPingId = UUID.randomUUID().toString().take(16),
            recordedAt = isoFormat.format(Date(location.time)),
            latitude = location.latitude,
            longitude = location.longitude,
            accuracyM = if (location.hasAccuracy()) location.accuracy else null,
            speedKph = speedKph,
            headingDeg = if (location.hasBearing()) location.bearing.toDouble() else null,
            batteryPct = batteryPercent(),
            isCharging = isCharging(),
            // Reported straight through. The server treats this as its primary
            // spoofing signal, and an honest client has no reason to hide it.
            mockLocation = isMock(location),
        )

        synchronized(queue) {
            while (queue.size >= QUEUE_LIMIT) {
                queue.pollFirst()
            }
            queue.addLast(ping)
            queueDepth = queue.size
        }

        updateNotification(speedKph)

        // Upload promptly so dispatch gets real-time position updates
        scope.launch {
            try {
                flush()
            } catch (e: Exception) {
                lastError = e.message
            }
        }
    }

    private suspend fun flush() {
        try {
            val token = Session.token ?: return

            val batch: List<QueuedPing> = synchronized(queue) {
                if (queue.isEmpty()) return
                val take = minOf(BATCH_SIZE, queue.size)
                (0 until take).mapNotNull { queue.pollFirst() }
            }
            if (batch.isEmpty()) return

            val result = CargoResQApi.uploadPings(token, Session.deviceId, batch)
            result.onSuccess {
                lastUploadedAt = System.currentTimeMillis()
                lastError = null
                synchronized(queue) { queueDepth = queue.size }
            }.onFailure { error ->
                // Put them back at the front, oldest first, so nothing is lost to
                // a transient outage. Order is preserved because the batch was
                // taken from the front.
                synchronized(queue) {
                    batch.asReversed().forEach { queue.addFirst(it) }
                    while (queue.size > QUEUE_LIMIT) {
                        queue.pollFirst()
                    }
                    queueDepth = queue.size
                }
                lastError = error.message
            }
        } catch (e: Exception) {
            lastError = e.message
        }
    }

    // -- device state -----------------------------------------------------

    private fun batteryPercent(): Double? = try {
        val manager = getSystemService(Context.BATTERY_SERVICE) as BatteryManager
        manager.getIntProperty(BatteryManager.BATTERY_PROPERTY_CAPACITY)
            .takeIf { it in 0..100 }?.toDouble()
    } catch (e: Exception) {
        null
    }

    private fun isCharging(): Boolean? = try {
        val status = registerReceiver(null, IntentFilter(Intent.ACTION_BATTERY_CHANGED))
            ?.getIntExtra(BatteryManager.EXTRA_STATUS, -1)
        when (status) {
            BatteryManager.BATTERY_STATUS_CHARGING,
            BatteryManager.BATTERY_STATUS_FULL -> true
            -1, null -> null
            else -> false
        }
    } catch (e: Exception) {
        null
    }

    @Suppress("DEPRECATION")
    private fun isMock(location: Location): Boolean =
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            location.isMock
        } else {
            location.isFromMockProvider
        }

    // -- notification -----------------------------------------------------

    private fun buildNotification(text: String): Notification {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val channel = NotificationChannel(
                CHANNEL_ID,
                "Position sharing",
                NotificationManager.IMPORTANCE_LOW,
            ).apply {
                description = "Shown while CargoResQ is sharing your position with dispatch."
                setShowBadge(false)
            }
            (getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager)
                .createNotificationChannel(channel)
        }

        val open = PendingIntent.getActivity(
            this,
            0,
            Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )

        return NotificationCompat.Builder(this, CHANNEL_ID)
            .setContentTitle("CargoResQ on duty")
            .setContentText(text)
            .setSmallIcon(R.drawable.ic_reefer)
            .setOngoing(true)
            .setPriority(NotificationCompat.PRIORITY_LOW)
            .setContentIntent(open)
            .build()
    }

    private fun updateNotification(speedKph: Double?) {
        val queued = queueDepth
        val text = when {
            queued > 10 -> "$queued fixes waiting for signal"
            speedKph != null && speedKph > MOVING_SPEED_KPH ->
                "Moving at " + speedKph.toInt() + " km/h"
            else -> "Parked - sharing position with dispatch"
        }
        runCatching {
            (getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager)
                .notify(NOTIFICATION_ID, buildNotification(text))
        }
    }
}
