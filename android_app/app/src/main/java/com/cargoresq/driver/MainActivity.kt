package com.cargoresq.driver

import android.Manifest
import android.annotation.SuppressLint
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.Color
import android.location.Location
import android.location.LocationListener
import android.location.LocationManager
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.*
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import androidx.lifecycle.lifecycleScope
import androidx.recyclerview.widget.LinearLayoutManager
import androidx.recyclerview.widget.RecyclerView
import androidx.swiperefreshlayout.widget.SwipeRefreshLayout
import okhttp3.HttpUrl.Companion.toHttpUrlOrNull
import com.cargoresq.driver.api.CargoResQApi
import com.cargoresq.driver.model.*
import com.cargoresq.driver.telemetry.TelemetryService
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import android.os.Looper
import com.google.android.gms.location.FusedLocationProviderClient
import com.google.android.gms.location.LocationCallback
import com.google.android.gms.location.LocationRequest
import com.google.android.gms.location.LocationResult
import com.google.android.gms.location.LocationServices
import com.google.android.gms.location.Priority
import org.osmdroid.config.Configuration
import org.osmdroid.tileprovider.tilesource.TileSourceFactory
import org.osmdroid.util.GeoPoint
import org.osmdroid.views.MapView
import org.osmdroid.views.overlay.Marker
import java.text.SimpleDateFormat
import java.util.*

/**
 * The driver app.
 *
 * Five screens on one activity: where you are, what jobs are on offer, the
 * emergency button, safety guidance, and your account.
 *
 * What changed from the previous version is less the layout than the honesty.
 * Every screen showed hardcoded values -- a fixed shipment, a fixed
 * temperature, a fixed earnings figure -- and the API client turned every
 * error into a successful-looking fake session. Nothing here invents data: if
 * the server cannot be reached, the screen says so.
 */
class MainActivity : AppCompatActivity() {

    private lateinit var contentFrame: FrameLayout
    private lateinit var navBar: LinearLayout

    private var currentTab = TAB_HOME
    private var profile: DriverProfile? = null
    private var shipment: ShipmentInfo? = null
    private var incident: IncidentInfo? = null
    private var offers: List<OfferInfo> = emptyList()
    private var emergencyNumbers: List<EmergencyNumber> = emptyList()

    private var onDuty = false
    private var lastKnownLocation: Location? = null

    private var homeMap: MapView? = null
    private var mapFusedClient: FusedLocationProviderClient? = null
    private var mapFusedCallback: LocationCallback? = null
    private var mapLocationManager: LocationManager? = null
    private var mapLocationListener: LocationListener? = null
    /** Centre on the driver once, then leave the map where they put it. */
    private var hasCentredOnce = false
    private var driverMarker: Marker? = null
    private var tickerJob: Job? = null

    private val isoFormat = SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss.SSS'Z'", Locale.US).apply {
        timeZone = TimeZone.getTimeZone("UTC")
    }

    companion object {
        private const val TAB_HOME = 0
        private const val TAB_JOBS = 1
        private const val TAB_SOS = 2
        private const val TAB_SAFETY = 3
        private const val TAB_ACCOUNT = 4
    }

    private val locationPermissionLauncher = registerForActivityResult(
        ActivityResultContracts.RequestMultiplePermissions()
    ) { granted ->
        val allowed = granted[Manifest.permission.ACCESS_FINE_LOCATION] == true ||
            granted[Manifest.permission.ACCESS_COARSE_LOCATION] == true
        if (allowed) {
            startDuty()
        } else {
            // Explain rather than silently do nothing. Without location the
            // core promise of the product cannot be kept.
            AlertDialog.Builder(this)
                .setTitle("Location is needed")
                .setMessage(getString(R.string.permission_location_rationale))
                .setPositiveButton("OK", null)
                .show()
        }
    }

    private val notificationPermissionLauncher = registerForActivityResult(
        ActivityResultContracts.RequestPermission()
    ) { /* The foreground service runs either way; the notification is the OS's. */ }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        Session.init(applicationContext)

        // osmdroid needs a user agent per OpenStreetMap's tile policy, and a
        // cache directory, before any MapView is inflated.
        Configuration.getInstance().apply {
            load(applicationContext, getSharedPreferences("osmdroid", Context.MODE_PRIVATE))
            userAgentValue = packageName
            osmdroidBasePath = cacheDir
            osmdroidTileCache = java.io.File(cacheDir, "tiles")
        }

        setContentView(R.layout.activity_main)
        contentFrame = findViewById(R.id.content_frame)
        navBar = findViewById(R.id.floating_nav_bar)

        setupNavigation()

        if (Session.isSignedIn) {
            showApp()
        } else {
            showAuth()
        }
    }

    override fun onResume() {
        super.onResume()
        homeMap?.onResume()

        // onPause dropped the location subscription; pick it up again so the
        // pin keeps following after the driver returns from another app.
        val map = homeMap
        val status = contentFrame.findViewById<TextView>(R.id.text_map_status)
        if (map != null && status != null) {
            startLocationTicker(map, status)
        }

        // Offers expire and responders commit while the app is in the
        // background, so what is on screen is stale by definition.
        if (Session.isSignedIn) {
            loadEverything()
        }
    }

    override fun onPause() {
        // Stop consuming GPS the moment the screen is not showing it. The
        // duty service keeps its own subscription; this one is only for the
        // map the driver is looking at.
        stopLocationTicker()
        homeMap?.onPause()
        super.onPause()
    }

    override fun onDestroy() {
        stopLocationTicker()
        super.onDestroy()
    }

    // ------------------------------------------------------------------
    // Navigation
    // ------------------------------------------------------------------

    private fun setupNavigation() {
        findViewById<LinearLayout>(R.id.tab_home).setOnClickListener { switchTab(TAB_HOME) }
        findViewById<LinearLayout>(R.id.tab_jobs).setOnClickListener { switchTab(TAB_JOBS) }
        findViewById<LinearLayout>(R.id.tab_sos).setOnClickListener { switchTab(TAB_SOS) }
        findViewById<LinearLayout>(R.id.tab_safety).setOnClickListener { switchTab(TAB_SAFETY) }
        findViewById<LinearLayout>(R.id.tab_account).setOnClickListener { switchTab(TAB_ACCOUNT) }
    }

    private fun switchTab(index: Int) {
        currentTab = index
        updateNavStyles()
        contentFrame.removeAllViews()
        stopLocationTicker()
        homeMap = null
        driverMarker = null
        hasCentredOnce = false

        val inflater = LayoutInflater.from(this)
        when (index) {
            TAB_HOME -> renderHome(inflater)
            TAB_JOBS -> renderJobs(inflater)
            TAB_SOS -> renderSos(inflater)
            TAB_SAFETY -> renderSafety(inflater)
            TAB_ACCOUNT -> renderAccount(inflater)
        }
    }

    private fun updateNavStyles() {
        val active = ContextCompat.getColor(this, R.color.white)
        val muted = ContextCompat.getColor(this, R.color.muted)
        val pairs = listOf(
            TAB_HOME to (R.id.icon_home to R.id.text_home),
            TAB_JOBS to (R.id.icon_jobs to R.id.text_jobs),
            TAB_SOS to (R.id.icon_sos to R.id.text_sos),
            TAB_SAFETY to (R.id.icon_safety to R.id.text_safety),
            TAB_ACCOUNT to (R.id.icon_account to R.id.text_account),
        )
        pairs.forEach { (tab, ids) ->
            val isActive = tab == currentTab
            // SOS keeps its red tint even when inactive, so the eye finds it.
            val tint = when {
                tab == TAB_SOS -> ContextCompat.getColor(this, R.color.red_soft)
                isActive -> active
                else -> muted
            }
            findViewById<ImageView>(ids.first).setColorFilter(if (isActive) active else tint)
            findViewById<TextView>(ids.second).setTextColor(if (isActive) active else tint)
        }
    }

    // ------------------------------------------------------------------
    // Auth
    // ------------------------------------------------------------------

    private fun showAuth() {
        navBar.visibility = View.GONE
        contentFrame.removeAllViews()
        val view = LayoutInflater.from(this).inflate(R.layout.view_auth, contentFrame, false)

        val baseUrlInput = view.findViewById<EditText>(R.id.input_base_url)
        val emailInput = view.findViewById<EditText>(R.id.input_email)
        val passwordInput = view.findViewById<EditText>(R.id.input_password)
        val errorText = view.findViewById<TextView>(R.id.text_auth_error)
        val button = view.findViewById<Button>(R.id.btn_sign_in)
        val progress = view.findViewById<ProgressBar>(R.id.progress_auth)

        val presetUsb = view.findViewById<Button>(R.id.btn_preset_usb)
        val presetWifi = view.findViewById<Button>(R.id.btn_preset_wifi)
        val presetEmu = view.findViewById<Button>(R.id.btn_preset_emu)

        presetUsb?.setOnClickListener { baseUrlInput.setText("http://127.0.0.1:8000") }
        presetWifi?.setOnClickListener { baseUrlInput.setText("http://172.18.228.204:8000") }
        presetEmu?.setOnClickListener { baseUrlInput.setText("http://10.0.2.2:8000") }

        // If on real hardware and still pointing at emulator IP, default to Wi-Fi LAN
        val initialUrl = if (Session.baseUrl == "http://10.0.2.2:8000" && !android.os.Build.FINGERPRINT.startsWith("generic")) {
            "http://172.18.228.204:8000"
        } else {
            Session.baseUrl
        }
        baseUrlInput.setText(initialUrl)

        button.setOnClickListener {
            val email = emailInput.text.toString().trim()
            val password = passwordInput.text.toString()
            if (email.isEmpty() || password.isEmpty()) {
                errorText.text = "Enter your email and password."
                errorText.visibility = View.VISIBLE
                return@setOnClickListener
            }

            val rawUrl = baseUrlInput.text.toString().trim()
            if (rawUrl.isEmpty()) {
                errorText.text = "Enter the server address (e.g. http://172.18.228.204:8000)."
                errorText.visibility = View.VISIBLE
                return@setOnClickListener
            }
            val cleanUrl = CargoResQApi.sanitizeBaseUrl(rawUrl)
            if (cleanUrl.toHttpUrlOrNull() == null) {
                errorText.text = "Invalid server address: $rawUrl. Include valid host and port."
                errorText.visibility = View.VISIBLE
                return@setOnClickListener
            }

            Session.baseUrl = cleanUrl
            errorText.visibility = View.GONE
            button.isEnabled = false
            progress.visibility = View.VISIBLE

            lifecycleScope.launch {
                val result = CargoResQApi.login(email, password)
                progress.visibility = View.GONE
                button.isEnabled = true

                result.onSuccess { session ->
                    Session.save(session)
                    showApp()
                }.onFailure { error ->
                    val tip = if (cleanUrl.contains("10.0.2.2")) {
                        "\nTip: 10.0.2.2 only works inside Android Emulator. On a phone, tap 'Wi-Fi' or 'USB' above."
                    } else if (cleanUrl.contains("127.0.0.1") || cleanUrl.contains("localhost")) {
                        "\nTip: For USB connection, run connect_phone_usb.bat (adb reverse tcp:8000 tcp:8000) on your PC."
                    } else {
                        "\nTip: Ensure your PC backend is running and both devices share the same network."
                    }
                    errorText.text = (error.message ?: "Sign in failed.") + tip
                    errorText.visibility = View.VISIBLE
                }
            }
        }

        contentFrame.addView(view)
    }

    private fun showApp() {
        navBar.visibility = View.VISIBLE
        switchTab(TAB_HOME)
        loadEverything()
    }

    private fun signOut() {
        stopDuty()
        Session.clear()
        profile = null
        shipment = null
        incident = null
        offers = emptyList()
        showAuth()
    }

    // ------------------------------------------------------------------
    // Data
    // ------------------------------------------------------------------

    private fun loadEverything(onDone: (() -> Unit)? = null) {
        val token = Session.token ?: return
        lifecycleScope.launch {
            CargoResQApi.profile(token).onSuccess { profile = it }
            CargoResQApi.activeShipment(token).onSuccess { shipment = it }
            CargoResQApi.activeIncident(token).onSuccess { incident = it }
            CargoResQApi.offerInbox(token).onSuccess { offers = it }
            if (emergencyNumbers.isEmpty()) {
                CargoResQApi.emergencyNumbers().onSuccess { emergencyNumbers = it }
            }
            if (currentTab == TAB_HOME && homeMap != null) {
                updateHomeFields()
            } else {
                switchTab(currentTab)
            }
            onDone?.invoke()
        }
    }

    private fun updateHomeFields() {
        val root = if (contentFrame.childCount > 0) contentFrame.getChildAt(0) else return
        root.findViewById<TextView>(R.id.text_driver_name)?.text =
            profile?.name ?: Session.current?.driverName ?: "Driver"
        val truck = profile?.truck
        root.findViewById<TextView>(R.id.text_truck_summary)?.text = when {
            truck == null -> "No truck assigned"
            truck.refrigerated -> truck.registrationNumber + " - reefer"
            else -> truck.registrationNumber
        }
        val dutyBadge = root.findViewById<TextView>(R.id.badge_duty)
        val dutyButton = root.findViewById<Button>(R.id.btn_duty_toggle)
        val telemetryStatus = root.findViewById<TextView>(R.id.text_telemetry_status)
        if (dutyBadge != null && dutyButton != null && telemetryStatus != null) {
            renderDutyState(dutyBadge, dutyButton, telemetryStatus)
        }
    }

    // ------------------------------------------------------------------
    // Home
    // ------------------------------------------------------------------

    @SuppressLint("SetTextI18n")
    private fun renderHome(inflater: LayoutInflater) {
        val view = inflater.inflate(R.layout.view_home, contentFrame, false)

        view.findViewById<TextView>(R.id.text_driver_name).text =
            profile?.name ?: Session.current?.driverName ?: "Driver"

        val truck = profile?.truck
        view.findViewById<TextView>(R.id.text_truck_summary).text = when {
            truck == null -> "No truck assigned"
            truck.refrigerated -> truck.registrationNumber + " - reefer"
            else -> truck.registrationNumber
        }

        val dutyBadge = view.findViewById<TextView>(R.id.badge_duty)
        val dutyButton = view.findViewById<Button>(R.id.btn_duty_toggle)
        val telemetryStatus = view.findViewById<TextView>(R.id.text_telemetry_status)
        renderDutyState(dutyBadge, dutyButton, telemetryStatus)

        dutyButton.setOnClickListener {
            if (onDuty) {
                stopDuty()
            } else {
                requestLocationThenStart()
            }
            renderDutyState(dutyBadge, dutyButton, telemetryStatus)
        }

        // -- the map --
        val map = view.findViewById<MapView>(R.id.map_home)
        val mapStatus = view.findViewById<TextView>(R.id.text_map_status)
        setupMap(map, mapStatus)
        homeMap = map
        view.findViewById<Button>(R.id.btn_recentre).setOnClickListener { recentreMap() }

        // -- active incident --
        incident?.let { inc ->
            view.findViewById<LinearLayout>(R.id.card_incident).visibility = View.VISIBLE
            view.findViewById<TextView>(R.id.text_incident_state).text =
                inc.state.replace('_', ' ').lowercase().replaceFirstChar { it.uppercase() }
            val spoilage = inc.minutesUntilSpoilage
            view.findViewById<TextView>(R.id.text_incident_detail).text = buildString {
                append(inc.cargoType ?: "Your load")
                if (spoilage != null) {
                    append(" - ")
                    append(spoilage.toInt())
                    append(" min before the cargo is at risk")
                }
            }
        }

        // -- assigned load --
        val cargoTitle = view.findViewById<TextView>(R.id.text_cargo_type)
        val cargoDetail = view.findViewById<TextView>(R.id.text_cargo_detail)
        val conditionsRow = view.findViewById<LinearLayout>(R.id.row_cargo_conditions)
        conditionsRow.removeAllViews()

        val load = shipment
        if (load == null) {
            cargoTitle.text = "No load assigned"
            cargoDetail.text = "Your dispatcher has not assigned a shipment to this truck."
        } else {
            cargoTitle.text = load.cargoType
            cargoDetail.text =
                formatWeight(load.weightKg) + " - " + formatMoney(load.valueInr) + " declared value"
            if (load.requiresRefrigeration) {
                val limit = load.requiredMaxTempC
                conditionsRow.addView(
                    chip(if (limit != null) "Keep below " + limit + "C" else "Refrigerated",
                        R.color.teal, R.drawable.bg_badge_teal)
                )
            }
            if (load.isHazmat) {
                conditionsRow.addView(chip("Hazmat", R.color.red, R.drawable.bg_badge_red))
            }
        }

        // -- manual condition logging, only for a reefer load --
        val conditionCard = view.findViewById<LinearLayout>(R.id.card_condition)
        if (load != null && load.requiresRefrigeration) {
            conditionCard.visibility = View.VISIBLE
            val input = view.findViewById<EditText>(R.id.input_temperature)
            view.findViewById<Button>(R.id.btn_log_temperature).setOnClickListener {
                val value = input.text.toString().toDoubleOrNull()
                if (value == null) {
                    toast("Enter the temperature shown on the gauge.")
                    return@setOnClickListener
                }
                logTemperature(load, value) { input.setText("") }
            }
        }

        view.findViewById<Button>(R.id.btn_report_breakdown).setOnClickListener {
            confirmBreakdown()
        }

        val swipe = view.findViewById<SwipeRefreshLayout>(R.id.swipe_home)
        swipe.setOnRefreshListener { loadEverything { swipe.isRefreshing = false } }

        contentFrame.addView(view)
    }

    private fun renderDutyState(badge: TextView, button: Button, status: TextView) {
        if (onDuty) {
            badge.text = "ON DUTY"
            badge.setBackgroundResource(R.drawable.bg_badge_teal)
            badge.setTextColor(ContextCompat.getColor(this, R.color.teal))
            button.text = "Go off duty"
            button.setBackgroundResource(R.drawable.bg_pill_outline)
            button.setTextColor(ContextCompat.getColor(this, R.color.ink))

            val queued = TelemetryService.queueDepth
            val lastUpload = TelemetryService.lastUploadedAt
            status.text = when {
                TelemetryService.lastError != null ->
                    "Cannot reach dispatch. " + queued + " positions saved on this phone."
                queued > 0 -> queued.toString() + " positions waiting for signal."
                lastUpload > 0 -> "Dispatch has your position."
                else -> "Getting your first position..."
            }
        } else {
            badge.text = "OFF DUTY"
            badge.setBackgroundResource(R.drawable.bg_badge_amber)
            badge.setTextColor(ContextCompat.getColor(this, R.color.amber))
            button.text = "Go on duty"
            button.setBackgroundResource(R.drawable.bg_pill_primary)
            button.setTextColor(ContextCompat.getColor(this, R.color.white))
            status.text = "Position sharing is off. Dispatch cannot see where you are."
        }
    }

    // ------------------------------------------------------------------
    // Map
    // ------------------------------------------------------------------

    private fun setupMap(map: MapView, status: TextView) {
        map.setTileSource(TileSourceFactory.MAPNIK)
        map.setMultiTouchControls(true)
        map.setUseDataConnection(true)
        map.controller.setZoom(15.0)

        val known = lastKnownLocation ?: readLastKnownLocation()
        if (known != null) {
            lastKnownLocation = known
            val point = GeoPoint(known.latitude, known.longitude)
            map.controller.setCenter(point)
            placeDriverMarker(map, point)
            status.text = "Your position"
        } else {
            // A registration coordinate is not a position. Centre on the
            // country and say so rather than implying a fix we do not have.
            map.controller.setCenter(GeoPoint(20.5937, 78.9629))
            map.controller.setZoom(5.0)
            status.text = if (onDuty) "Waiting for GPS" else "Go on duty to show your position"
        }

        startLocationTicker(map, status)
    }

    private fun placeDriverMarker(map: MapView, point: GeoPoint) {
        try {
            val existing = driverMarker
            if (existing != null && map.overlays.contains(existing)) {
                existing.position = point
            } else {
                val marker = Marker(map).apply {
                    position = point
                    setAnchor(Marker.ANCHOR_CENTER, Marker.ANCHOR_BOTTOM)
                    title = profile?.truck?.registrationNumber ?: "You"
                }
                map.overlays.add(marker)
                driverMarker = marker
            }
            map.invalidate()
        } catch (e: Exception) {
            // Ignore concurrent draw errors
        }
    }

    /** Follow the device's position on the map while the screen is open. */
    /**
     * Keep the pin on the driver's actual position while the map is visible.
     *
     * Subscribes to live fixes rather than re-reading the last known one on a
     * timer. getLastKnownLocation returns whatever the system happens to have
     * cached, which off duty can be hours old and never changes -- so the pin
     * sat still while the driver drove, which is precisely the thing a map is
     * for.
     *
     * This runs whether or not the driver is on duty. Showing someone where
     * they are is not the same as reporting it to their employer: nothing
     * here uploads anything.
     */
    @SuppressLint("MissingPermission")
    private fun startLocationTicker(map: MapView, status: TextView) {
        stopLocationTicker()

        if (!hasLocationPermission()) {
            status.text = "Allow location to see yourself on the map"
            return
        }

        val onFix: (Location) -> Unit = { location ->
            lastKnownLocation = location
            val point = GeoPoint(location.latitude, location.longitude)
            placeDriverMarker(map, point)
            if (!hasCentredOnce) {
                map.controller.animateTo(point)
                map.controller.setZoom(16.0)
                hasCentredOnce = true
            }
            status.text = when {
                onDuty -> "Sharing your position"
                else -> "Your position"
            }
        }

        // Fused where Play Services exists; LocationManager otherwise, so the
        // map still follows on a device without Google's services.
        val started = try {
            val client = LocationServices.getFusedLocationProviderClient(this)
            val request = LocationRequest.Builder(Priority.PRIORITY_HIGH_ACCURACY, 3_000L)
                .setMinUpdateIntervalMillis(2_000L)
                .build()
            val callback = object : LocationCallback() {
                override fun onLocationResult(result: LocationResult) {
                    result.lastLocation?.let(onFix)
                }
            }
            client.requestLocationUpdates(request, callback, Looper.getMainLooper())
            mapFusedClient = client
            mapFusedCallback = callback
            true
        } catch (e: Exception) {
            false
        }

        if (!started) {
            try {
                val manager = getSystemService(Context.LOCATION_SERVICE) as LocationManager
                val provider = when {
                    manager.isProviderEnabled(LocationManager.GPS_PROVIDER) ->
                        LocationManager.GPS_PROVIDER
                    manager.isProviderEnabled(LocationManager.NETWORK_PROVIDER) ->
                        LocationManager.NETWORK_PROVIDER
                    else -> null
                }
                if (provider == null) {
                    status.text = "Location is switched off on this phone"
                } else {
                    val listener = LocationListener { onFix(it) }
                    manager.requestLocationUpdates(provider, 3_000L, 5f, listener,
                        Looper.getMainLooper())
                    mapLocationManager = manager
                    mapLocationListener = listener
                }
            } catch (e: Exception) {
                status.text = "Could not start location updates"
            }
        }

        // Show something immediately rather than waiting for the first fix.
        readLastKnownLocation()?.let(onFix)
    }

    private fun stopLocationTicker() {
        tickerJob?.cancel()
        tickerJob = null
        mapFusedCallback?.let { callback ->
            runCatching { mapFusedClient?.removeLocationUpdates(callback) }
        }
        mapFusedCallback = null
        mapFusedClient = null
        mapLocationListener?.let { listener ->
            runCatching { mapLocationManager?.removeUpdates(listener) }
        }
        mapLocationListener = null
        mapLocationManager = null
    }

    private fun recentreMap() {
        val location = lastKnownLocation ?: readLastKnownLocation()
        if (location == null) {
            toast("No GPS fix yet.")
            return
        }
        homeMap?.controller?.animateTo(GeoPoint(location.latitude, location.longitude))
        homeMap?.controller?.setZoom(16.0)
    }

    @SuppressLint("MissingPermission")
    private fun readLastKnownLocation(): Location? {
        val fromService = TelemetryService.lastLocation
        if (fromService != null) return fromService
        if (!hasLocationPermission()) return null
        return try {
            val manager = getSystemService(Context.LOCATION_SERVICE) as LocationManager
            listOf(LocationManager.GPS_PROVIDER, LocationManager.NETWORK_PROVIDER)
                .mapNotNull { runCatching { manager.getLastKnownLocation(it) }.getOrNull() }
                .maxByOrNull { it.time }
        } catch (e: Exception) {
            null
        }
    }

    // ------------------------------------------------------------------
    // Duty / telemetry
    // ------------------------------------------------------------------

    private fun hasLocationPermission(): Boolean =
        ContextCompat.checkSelfPermission(this, Manifest.permission.ACCESS_FINE_LOCATION) ==
            PackageManager.PERMISSION_GRANTED ||
            ContextCompat.checkSelfPermission(this, Manifest.permission.ACCESS_COARSE_LOCATION) ==
            PackageManager.PERMISSION_GRANTED

    private fun requestLocationThenStart() {
        if (hasLocationPermission()) {
            startDuty()
            return
        }
        locationPermissionLauncher.launch(
            arrayOf(
                Manifest.permission.ACCESS_FINE_LOCATION,
                Manifest.permission.ACCESS_COARSE_LOCATION,
            )
        )
    }

    private fun startDuty() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU &&
            ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS) !=
            PackageManager.PERMISSION_GRANTED
        ) {
            notificationPermissionLauncher.launch(Manifest.permission.POST_NOTIFICATIONS)
        }
        TelemetryService.start(this)
        onDuty = true
        toast("On duty. Dispatch can see your position.")
        if (currentTab == TAB_HOME && homeMap != null) {
            updateHomeFields()
        } else {
            switchTab(TAB_HOME)
        }

        // Transmit initial beacon fix immediately
        val location = readLastKnownLocation()
        val token = Session.token
        if (location != null && token != null) {
            lastKnownLocation = location
            homeMap?.let { placeDriverMarker(it, GeoPoint(location.latitude, location.longitude)) }
            lifecycleScope.launch {
                val ping = QueuedPing(
                    clientPingId = UUID.randomUUID().toString().take(16),
                    recordedAt = isoFormat.format(Date(location.time)),
                    latitude = location.latitude,
                    longitude = location.longitude,
                    accuracyM = if (location.hasAccuracy()) location.accuracy else null,
                    speedKph = if (location.hasSpeed()) location.speed * 3.6 else 0.0,
                    headingDeg = if (location.hasBearing()) location.bearing.toDouble() else null,
                    batteryPct = null,
                    isCharging = null,
                    mockLocation = false,
                )
                CargoResQApi.uploadPings(token, Session.deviceId, listOf(ping))
            }
        }
    }

    private fun stopDuty() {
        TelemetryService.stop(this)
        onDuty = false
    }

    private fun logTemperature(load: ShipmentInfo, value: Double, onDone: () -> Unit) {
        val token = Session.token ?: return
        lifecycleScope.launch {
            val result = CargoResQApi.uploadReading(
                token = token,
                sensorId = Session.deviceId,
                shipmentId = load.id,
                recordedAt = isoFormat.format(Date()),
                temperatureC = value,
                doorOpen = null,
                reeferState = null,
                clientReadingId = UUID.randomUUID().toString().take(16),
            )
            result.onSuccess {
                onDone()
                val limit = load.requiredMaxTempC
                if (limit != null && value > limit) {
                    // Do not soften this. A breach is what disputes a payment.
                    AlertDialog.Builder(this@MainActivity)
                        .setTitle("Above the limit")
                        .setMessage(
                            "You recorded " + value + "C against a limit of " + limit +
                                "C. Dispatch has been alerted. Keep the doors shut and " +
                                "record again in 15 minutes."
                        )
                        .setPositiveButton("Understood", null)
                        .show()
                } else {
                    toast("Recorded " + value + "C.")
                }
            }.onFailure { toast(it.message ?: "Could not record the reading.") }
        }
    }

    private fun confirmBreakdown() {
        val location = lastKnownLocation ?: readLastKnownLocation()
        if (location == null) {
            // Refuse rather than send a made-up coordinate. The previous build
            // posted a fixed 18.7511, 73.3422 for every breakdown, anywhere in
            // the country.
            AlertDialog.Builder(this)
                .setTitle("No position yet")
                .setMessage(
                    "CargoResQ needs your GPS position to send help to the right place. " +
                        "Go on duty and wait for a fix, then try again."
                )
                .setPositiveButton("OK", null)
                .show()
            return
        }

        AlertDialog.Builder(this)
            .setTitle("Report a breakdown?")
            .setMessage(
                "Your dispatcher will be told your truck cannot continue, and " +
                    "compatible trucks nearby will be asked to help.\n\nIf anyone is " +
                    "hurt, use SOS instead."
            )
            .setPositiveButton("Report") { _, _ -> sendBreakdown(location) }
            .setNegativeButton("Cancel", null)
            .show()
    }

    private fun sendBreakdown(location: Location) {
        val token = Session.token ?: return
        lifecycleScope.launch {
            val result = CargoResQApi.reportBreakdown(
                token = token,
                latitude = location.latitude,
                longitude = location.longitude,
                hoursToSpoilage = null,
                clientRequestId = "android-" + UUID.randomUUID().toString().take(12),
            )
            result.onSuccess {
                toast("Breakdown reported. Dispatch is looking for help.")
                loadEverything()
            }.onFailure { error ->
                // The failure is shown. It used to return success regardless.
                AlertDialog.Builder(this@MainActivity)
                    .setTitle("Not reported")
                    .setMessage(
                        (error.message ?: "The report did not go through.") +
                            "\n\nCall your dispatcher directly."
                    )
                    .setPositiveButton("OK", null)
                    .show()
            }
        }
    }

    // ------------------------------------------------------------------
    // Jobs
    // ------------------------------------------------------------------

    private fun renderJobs(inflater: LayoutInflater) {
        val view = inflater.inflate(R.layout.view_jobs, contentFrame, false)
        val list = view.findViewById<RecyclerView>(R.id.list_jobs)
        val empty = view.findViewById<TextView>(R.id.text_jobs_empty)

        val live = offers.filter { it.state == "PENDING" || it.state == "OWNER_CONFIRMED" }
        empty.visibility = if (live.isEmpty()) View.VISIBLE else View.GONE

        list.layoutManager = LinearLayoutManager(this)
        list.adapter = JobAdapter(live)

        val swipe = view.findViewById<SwipeRefreshLayout>(R.id.swipe_jobs)
        swipe.setOnRefreshListener { loadEverything { swipe.isRefreshing = false } }

        contentFrame.addView(view)
    }

    private inner class JobAdapter(private val items: List<OfferInfo>) :
        RecyclerView.Adapter<JobAdapter.JobViewHolder>() {

        inner class JobViewHolder(view: View) : RecyclerView.ViewHolder(view) {
            val eta: TextView = view.findViewById(R.id.text_job_eta)
            val detail: TextView = view.findViewById(R.id.text_job_detail)
            val expiry: TextView = view.findViewById(R.id.text_job_expiry)
            val badge: TextView = view.findViewById(R.id.badge_job_state)
            val accept: Button = view.findViewById(R.id.btn_job_accept)
            val decline: Button = view.findViewById(R.id.btn_job_decline)
        }

        override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): JobViewHolder =
            JobViewHolder(
                LayoutInflater.from(parent.context).inflate(R.layout.item_job, parent, false)
            )

        override fun getItemCount(): Int = items.size

        @SuppressLint("SetTextI18n")
        override fun onBindViewHolder(holder: JobViewHolder, position: Int) {
            val offer = items[position]
            val eta = offer.etaMinutes
            holder.eta.text = if (eta != null) eta.toInt().toString() + " min away" else "Nearby"
            holder.detail.text = buildString {
                offer.distanceKm?.let { append(String.format(Locale.US, "%.1f km", it)) }
                if (offer.ownerConfirmed) {
                    append(if (isEmpty()) "" else "  -  ")
                    append("Customer is waiting on you")
                }
            }

            holder.badge.text = offer.state.replace('_', ' ')
            holder.expiry.text = describeExpiry(offer.expiresAt)

            holder.accept.setOnClickListener { acceptOffer(offer) }
            holder.decline.setOnClickListener { declineOffer(offer) }
        }
    }

    /** How long is left to answer, in words. */
    private fun describeExpiry(expiresAt: String?): String {
        if (expiresAt == null) return ""
        val parsed = runCatching {
            val normalised = expiresAt.replace("Z", "+0000").replace(Regex("([+-]\\d{2}):(\\d{2})$"), "$1$2")
            SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss[.SSS]Z", Locale.US).parse(normalised)
        }.getOrNull() ?: return ""

        val seconds = (parsed.time - System.currentTimeMillis()) / 1000
        return when {
            seconds <= 0 -> "Expired"
            seconds < 60 -> seconds.toString() + " seconds to answer"
            else -> (seconds / 60).toString() + " minutes to answer"
        }
    }

    private fun acceptOffer(offer: OfferInfo) {
        val token = Session.token ?: return
        lifecycleScope.launch {
            CargoResQApi.acceptOffer(token, offer.id)
                .onSuccess { bound ->
                    // Accepting is half a handshake. Saying so prevents a
                    // driver setting off for a job that is not yet theirs.
                    toast(
                        if (bound) "Job confirmed. Head to the pickup."
                        else "Accepted. Waiting for the customer to confirm."
                    )
                    loadEverything()
                }
                .onFailure { toast(it.message ?: "Could not accept.") }
        }
    }

    private fun declineOffer(offer: OfferInfo) {
        val token = Session.token ?: return
        lifecycleScope.launch {
            CargoResQApi.declineOffer(token, offer.id, "Declined by driver")
                .onSuccess { loadEverything() }
                .onFailure { toast(it.message ?: "Could not decline.") }
        }
    }

    // ------------------------------------------------------------------
    // SOS
    // ------------------------------------------------------------------

    private var selectedCategory: SosCategory = SosCategory.MECHANICAL

    @SuppressLint("SetTextI18n")
    private fun renderSos(inflater: LayoutInflater) {
        val view = inflater.inflate(R.layout.view_sos, contentFrame, false)

        view.findViewById<Button>(R.id.btn_call_112).setOnClickListener { dial("112") }

        // Quick-dial row for the specific services.
        val numbersRow = view.findViewById<LinearLayout>(R.id.row_emergency_numbers)
        numbersRow.removeAllViews()
        val numbers = emergencyNumbers.ifEmpty { defaultEmergencyNumbers() }
        numbers.filter { !it.primary }.forEach { number ->
            numbersRow.addView(dialChip(number))
        }

        // Category tiles.
        val grid = view.findViewById<GridLayout>(R.id.grid_sos_categories)
        grid.removeAllViews()
        val tiles = mutableMapOf<SosCategory, TextView>()
        SosCategory.values().forEach { category ->
            val tile = categoryTile(category)
            tile.isSelected = category == selectedCategory
            tile.setOnClickListener {
                selectedCategory = category
                tiles.forEach { (c, t) -> t.isSelected = c == category }
            }
            tiles[category] = tile
            grid.addView(tile)
        }

        // Location, read now and refreshed by the ticker.
        val locationText = view.findViewById<TextView>(R.id.text_sos_location)
        val location = lastKnownLocation ?: readLastKnownLocation()
        locationText.text = if (location != null) {
            lastKnownLocation = location
            String.format(Locale.US, "%.5f, %.5f", location.latitude, location.longitude) +
                "  (accurate to " + location.accuracy.toInt() + " m)"
        } else {
            "No GPS fix. Describe where you are below so help can find you."
        }

        val resultText = view.findViewById<TextView>(R.id.text_sos_result)
        view.findViewById<Button>(R.id.btn_send_sos).setOnClickListener {
            sendSos(view, resultText)
        }

        contentFrame.addView(view)
    }

    private fun categoryTile(category: SosCategory): TextView {
        val tile = TextView(this).apply {
            text = category.label
            gravity = android.view.Gravity.CENTER
            setBackgroundResource(R.drawable.bg_sos_tile)
            setTextColor(ContextCompat.getColor(this@MainActivity, R.color.ink))
            textSize = 13f
            setPadding(8, 24, 8, 24)
        }
        val params = GridLayout.LayoutParams().apply {
            width = 0
            height = GridLayout.LayoutParams.WRAP_CONTENT
            columnSpec = GridLayout.spec(GridLayout.UNDEFINED, 1f)
            setMargins(6, 6, 6, 6)
        }
        tile.layoutParams = params
        return tile
    }

    private fun dialChip(number: EmergencyNumber): View {
        val chip = TextView(this).apply {
            text = number.label + "  " + number.number
            setBackgroundResource(R.drawable.bg_pill_outline)
            setTextColor(ContextCompat.getColor(this@MainActivity, R.color.ink))
            textSize = 12f
            setPadding(28, 18, 28, 18)
            setOnClickListener { dial(number.number) }
        }
        val params = LinearLayout.LayoutParams(
            LinearLayout.LayoutParams.WRAP_CONTENT,
            LinearLayout.LayoutParams.WRAP_CONTENT,
        ).apply { setMargins(0, 0, 10, 0) }
        chip.layoutParams = params
        return chip
    }

    /**
     * Open the dialer with the number filled in.
     *
     * ACTION_DIAL, never ACTION_CALL: the driver presses the green button.
     * Placing an emergency call automatically is not a decision an app should
     * make, and the app does not hold CALL_PHONE.
     */
    private fun dial(number: String) {
        try {
            startActivity(Intent(Intent.ACTION_DIAL, Uri.parse("tel:" + number)))
        } catch (e: Exception) {
            toast("No dialler on this device. Call " + number + " manually.")
        }
    }

    private fun defaultEmergencyNumbers(): List<EmergencyNumber> = listOf(
        EmergencyNumber("All emergencies", "112", true),
        EmergencyNumber("Ambulance", "108", false),
        EmergencyNumber("Police", "100", false),
        EmergencyNumber("Fire", "101", false),
        EmergencyNumber("Highway", "1033", false),
    )

    private fun sendSos(view: View, resultText: TextView) {
        val token = Session.token ?: return
        val location = lastKnownLocation ?: readLastKnownLocation()

        if (location == null) {
            AlertDialog.Builder(this)
                .setTitle("No position")
                .setMessage(
                    "CargoResQ cannot tell anyone where you are without a GPS fix.\n\n" +
                        "Call 112 now and describe your location."
                )
                .setPositiveButton("Call 112") { _, _ -> dial("112") }
                .setNegativeButton("Cancel", null)
                .show()
            return
        }

        val severity = when (view.findViewById<RadioGroup>(R.id.group_severity).checkedRadioButtonId) {
            R.id.radio_critical -> SosSeverity.CRITICAL
            R.id.radio_moderate -> SosSeverity.MODERATE
            else -> SosSeverity.HIGH
        }

        val injured = view.findViewById<CheckBox>(R.id.check_injured).isChecked
        val condition = DriverCondition(
            personsAffected = view.findViewById<EditText>(R.id.input_persons)
                .text.toString().toIntOrNull(),
            isConscious = true,
            isTrapped = view.findViewById<CheckBox>(R.id.check_trapped).isChecked,
            isMobile = view.findViewById<CheckBox>(R.id.check_mobile).isChecked,
            severeBleeding = view.findViewById<CheckBox>(R.id.check_bleeding).isChecked,
            note = view.findViewById<EditText>(R.id.input_condition_note).text.toString(),
        )

        lifecycleScope.launch {
            val result = CargoResQApi.raiseSos(
                token = token,
                category = selectedCategory,
                severity = severity,
                latitude = location.latitude,
                longitude = location.longitude,
                accuracyM = if (location.hasAccuracy()) location.accuracy else null,
                landmarkNote = view.findViewById<EditText>(R.id.input_landmark).text.toString(),
                condition = condition,
                silentMode = view.findViewById<CheckBox>(R.id.check_silent).isChecked,
                clientRequestId = "sos-" + UUID.randomUUID().toString().take(14),
            )

            result.onSuccess { alert ->
                onDuty = true
                TelemetryService.start(this@MainActivity)
                resultText.visibility = View.VISIBLE
                resultText.setTextColor(ContextCompat.getColor(this@MainActivity, R.color.teal))
                resultText.text =
                    "Alert sent. Your company and nearby trucks have been told."

                // The confirmation restates what did and did not happen.
                val message = StringBuilder()
                message.append("Your company has been alerted")
                if (selectedCategory != SosCategory.POLICE_SECURITY) {
                    message.append(", and so have carriers with trucks near you")
                }
                message.append(".\n\n")
                if (!alert.psapDispatched) {
                    message.append(
                        "No ambulance, police or fire service has been called. " +
                            "If you need one, tap Call 112."
                    )
                }
                AlertDialog.Builder(this@MainActivity)
                    .setTitle("Alert sent")
                    .setMessage(message.toString())
                    .setPositiveButton("Call 112") { _, _ -> dial("112") }
                    .setNegativeButton("Close", null)
                    .show()
            }.onFailure { error ->
                resultText.visibility = View.VISIBLE
                resultText.setTextColor(ContextCompat.getColor(this@MainActivity, R.color.red))
                resultText.text = error.message ?: "The alert did not send."

                AlertDialog.Builder(this@MainActivity)
                    .setTitle("Alert NOT sent")
                    .setMessage(
                        (error.message ?: "Could not reach CargoResQ.") +
                            "\n\nCall 112 or your dispatcher directly. Do not wait."
                    )
                    .setPositiveButton("Call 112") { _, _ -> dial("112") }
                    .setNegativeButton("Close", null)
                    .show()
            }
        }
    }

    // ------------------------------------------------------------------
    // Safety
    // ------------------------------------------------------------------

    private fun renderSafety(inflater: LayoutInflater) {
        val view = inflater.inflate(R.layout.view_safety, contentFrame, false)
        val guidance = SafetyGuidance.forShipment(shipment)

        view.findViewById<TextView>(R.id.text_safety_context).text = guidance.context

        val dos = view.findViewById<LinearLayout>(R.id.list_dos)
        dos.removeAllViews()
        guidance.dos.forEach { dos.addView(bulletRow(it, R.color.teal)) }

        val donts = view.findViewById<LinearLayout>(R.id.list_donts)
        donts.removeAllViews()
        guidance.donts.forEach { donts.addView(bulletRow(it, R.color.red)) }

        val numbersList = view.findViewById<LinearLayout>(R.id.list_emergency_numbers)
        numbersList.removeAllViews()
        emergencyNumbers.ifEmpty { defaultEmergencyNumbers() }.forEach { number ->
            numbersList.addView(numberRow(number))
        }

        val contacts = view.findViewById<LinearLayout>(R.id.list_contacts)
        loadContacts(contacts)
        view.findViewById<Button>(R.id.btn_add_contact).setOnClickListener {
            promptAddContact(contacts)
        }

        contentFrame.addView(view)
    }

    private fun bulletRow(text: String, colorRes: Int): View {
        val row = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            setPadding(0, 10, 0, 10)
        }
        row.addView(TextView(this).apply {
            this.text = if (colorRes == R.color.red) "x" else "+"
            setTextColor(ContextCompat.getColor(this@MainActivity, colorRes))
            textSize = 15f
            setTypeface(typeface, android.graphics.Typeface.BOLD)
            layoutParams = LinearLayout.LayoutParams(60, LinearLayout.LayoutParams.WRAP_CONTENT)
        })
        row.addView(TextView(this).apply {
            this.text = text
            setTextColor(ContextCompat.getColor(this@MainActivity, R.color.ink))
            textSize = 14f
        })
        return row
    }

    private fun numberRow(number: EmergencyNumber): View {
        val row = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = android.view.Gravity.CENTER_VERTICAL
            setBackgroundResource(R.drawable.bg_card)
            setPadding(32, 28, 32, 28)
            layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT,
            ).apply { setMargins(0, 0, 0, 20) }
            setOnClickListener { dial(number.number) }
        }
        row.addView(TextView(this).apply {
            text = number.label
            setTextColor(ContextCompat.getColor(this@MainActivity, R.color.ink))
            textSize = 15f
            layoutParams = LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f)
        })
        row.addView(TextView(this).apply {
            text = number.number
            setTextColor(ContextCompat.getColor(this@MainActivity, R.color.teal))
            textSize = 17f
            setTypeface(typeface, android.graphics.Typeface.BOLD)
        })
        return row
    }

    private fun loadContacts(container: LinearLayout) {
        val token = Session.token ?: return
        container.removeAllViews()
        lifecycleScope.launch {
            CargoResQApi.emergencyContacts(token)
                .onSuccess { contacts ->
                    if (contacts.isEmpty()) {
                        container.addView(TextView(this@MainActivity).apply {
                            text = "No personal contacts yet. Add the people who should be " +
                                "told if something happens to you."
                            setTextColor(ContextCompat.getColor(this@MainActivity, R.color.muted))
                            textSize = 12f
                        })
                    }
                    contacts.forEach { contact ->
                        container.addView(
                            numberRow(
                                EmergencyNumber(
                                    label = contact.name +
                                        (contact.relationship?.let { " (" + it + ")" } ?: ""),
                                    number = contact.phone,
                                    primary = contact.isPrimary,
                                )
                            )
                        )
                    }
                }
                .onFailure {
                    container.addView(TextView(this@MainActivity).apply {
                        text = it.message ?: "Could not load contacts."
                        setTextColor(ContextCompat.getColor(this@MainActivity, R.color.red))
                        textSize = 12f
                    })
                }
        }
    }

    private fun promptAddContact(container: LinearLayout) {
        val layout = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(60, 40, 60, 10)
        }
        val nameInput = EditText(this).apply { hint = "Name" }
        val phoneInput = EditText(this).apply {
            hint = "Phone"
            inputType = android.text.InputType.TYPE_CLASS_PHONE
        }
        val relationInput = EditText(this).apply { hint = "Relationship (optional)" }
        layout.addView(nameInput)
        layout.addView(phoneInput)
        layout.addView(relationInput)

        AlertDialog.Builder(this)
            .setTitle("Add an emergency contact")
            .setView(layout)
            .setPositiveButton("Add") { _, _ ->
                val token = Session.token ?: return@setPositiveButton
                val name = nameInput.text.toString().trim()
                val phone = phoneInput.text.toString().trim()
                if (name.isEmpty() || phone.isEmpty()) {
                    toast("Name and phone are both needed.")
                    return@setPositiveButton
                }
                lifecycleScope.launch {
                    CargoResQApi.addEmergencyContact(
                        token, name, phone, relationInput.text.toString().trim()
                    ).onSuccess { loadContacts(container) }
                        .onFailure { toast(it.message ?: "Could not add the contact.") }
                }
            }
            .setNegativeButton("Cancel", null)
            .show()
    }

    // ------------------------------------------------------------------
    // Account
    // ------------------------------------------------------------------

    @SuppressLint("SetTextI18n")
    private fun renderAccount(inflater: LayoutInflater) {
        val view = inflater.inflate(R.layout.view_account, contentFrame, false)

        view.findViewById<TextView>(R.id.text_account_name).text =
            profile?.name ?: Session.current?.driverName ?: "Driver"
        view.findViewById<TextView>(R.id.text_account_email).text =
            profile?.email ?: Session.current?.email.orEmpty()

        val truck = profile?.truck
        view.findViewById<TextView>(R.id.text_account_truck).text =
            truck?.registrationNumber ?: "No truck assigned"
        view.findViewById<TextView>(R.id.text_account_truck_detail).text = when {
            truck == null -> "Ask your dispatcher to assign one."
            truck.refrigerated -> {
                val floor = truck.minTempC
                "Refrigerated" + (if (floor != null) " - holds down to " + floor + "C" else "")
            }
            else -> "Dry van"
        }

        view.findViewById<TextView>(R.id.text_account_telemetry).text = if (onDuty) {
            "On duty. " + TelemetryService.queueDepth + " positions queued."
        } else {
            "Off duty. Nothing is being sent."
        }

        view.findViewById<TextView>(R.id.text_account_server).text = Session.baseUrl
        view.findViewById<Button>(R.id.btn_sign_out).setOnClickListener {
            AlertDialog.Builder(this)
                .setTitle("Sign out?")
                .setMessage("Position sharing will stop and your dispatcher will lose sight of you.")
                .setPositiveButton("Sign out") { _, _ -> signOut() }
                .setNegativeButton("Stay", null)
                .show()
        }

        contentFrame.addView(view)
    }

    // ------------------------------------------------------------------
    // Helpers
    // ------------------------------------------------------------------

    private fun chip(text: String, colorRes: Int, backgroundRes: Int): View {
        val chip = TextView(this).apply {
            this.text = text
            setBackgroundResource(backgroundRes)
            setTextColor(ContextCompat.getColor(this@MainActivity, colorRes))
            textSize = 11f
            setTypeface(typeface, android.graphics.Typeface.BOLD)
            setPadding(24, 12, 24, 12)
        }
        chip.layoutParams = LinearLayout.LayoutParams(
            LinearLayout.LayoutParams.WRAP_CONTENT,
            LinearLayout.LayoutParams.WRAP_CONTENT,
        ).apply { setMargins(0, 0, 16, 0) }
        return chip
    }

    /** Indian numbering: lakhs and crores, because that is how it is read. */
    private fun formatMoney(amount: Double): String = when {
        amount >= 10_000_000 -> String.format(Locale.US, "INR %.2f Cr", amount / 10_000_000)
        amount >= 100_000 -> String.format(Locale.US, "INR %.2f L", amount / 100_000)
        else -> String.format(Locale.US, "INR %,.0f", amount)
    }

    private fun formatWeight(kg: Double): String =
        if (kg >= 1000) String.format(Locale.US, "%.1f t", kg / 1000)
        else String.format(Locale.US, "%.0f kg", kg)

    private fun toast(message: String) {
        Toast.makeText(this, message, Toast.LENGTH_LONG).show()
    }
}
