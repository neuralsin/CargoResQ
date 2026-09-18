package com.cargoresq.driver

import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.view.LayoutInflater
import android.view.View
import android.widget.*
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import com.cargoresq.driver.api.CargoResQApi
import com.cargoresq.driver.model.DriverSession
import com.google.android.material.bottomsheet.BottomSheetDialog
import java.util.concurrent.Executors

class MainActivity : AppCompatActivity() {

    private val executor = Executors.newSingleThreadExecutor()
    private val mainHandler = Handler(Looper.getMainLooper())

    private var currentSession: DriverSession? = null
    private var currentTab: Int = 0 // 0: Home, 1: Services, 2: Activity, 3: Account

    private lateinit var contentFrame: FrameLayout
    private lateinit var floatingNavBar: LinearLayout

    // Nav tabs
    private lateinit var tabHome: LinearLayout
    private lateinit var tabServices: LinearLayout
    private lateinit var tabActivity: LinearLayout
    private lateinit var tabAccount: LinearLayout

    private lateinit var iconHome: ImageView
    private lateinit var iconServices: ImageView
    private lateinit var iconActivity: ImageView
    private lateinit var iconAccount: ImageView

    private lateinit var textHome: TextView
    private lateinit var textServices: TextView
    private lateinit var textActivity: TextView
    private lateinit var textAccount: TextView

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        contentFrame = findViewById(R.id.content_frame)
        floatingNavBar = findViewById(R.id.floating_nav_bar)

        tabHome = findViewById(R.id.tab_home)
        tabServices = findViewById(R.id.tab_services)
        tabActivity = findViewById(R.id.tab_activity)
        tabAccount = findViewById(R.id.tab_account)

        iconHome = findViewById(R.id.icon_home)
        iconServices = findViewById(R.id.icon_services)
        iconActivity = findViewById(R.id.icon_activity)
        iconAccount = findViewById(R.id.icon_account)

        textHome = findViewById(R.id.text_home)
        textServices = findViewById(R.id.text_services)
        textActivity = findViewById(R.id.text_activity)
        textAccount = findViewById(R.id.text_account)

        setupNavigation()

        // Start with Auth screen if not logged in
        showAuthScreen()
    }

    private fun setupNavigation() {
        tabHome.setOnClickListener { switchTab(0) }
        tabServices.setOnClickListener { switchTab(1) }
        tabActivity.setOnClickListener { switchTab(2) }
        tabAccount.setOnClickListener { switchTab(3) }
    }

    private fun switchTab(tabIndex: Int) {
        currentTab = tabIndex
        updateNavPillStyles()

        contentFrame.removeAllViews()
        val inflater = LayoutInflater.from(this)

        when (tabIndex) {
            0 -> renderHomeScreen(inflater)
            1 -> renderServicesScreen(inflater)
            2 -> renderActivityScreen(inflater)
            3 -> renderAccountScreen(inflater)
        }
    }

    private fun updateNavPillStyles() {
        val activeColor = ContextCompat.getColor(this, R.color.uber_white)
        val mutedColor = ContextCompat.getColor(this, R.color.uber_gray_muted)

        iconHome.setColorFilter(if (currentTab == 0) activeColor else mutedColor)
        textHome.setTextColor(if (currentTab == 0) activeColor else mutedColor)

        iconServices.setColorFilter(if (currentTab == 1) activeColor else mutedColor)
        textServices.setTextColor(if (currentTab == 1) activeColor else mutedColor)

        iconActivity.setColorFilter(if (currentTab == 2) activeColor else mutedColor)
        textActivity.setTextColor(if (currentTab == 2) activeColor else mutedColor)

        iconAccount.setColorFilter(if (currentTab == 3) activeColor else mutedColor)
        textAccount.setTextColor(if (currentTab == 3) activeColor else mutedColor)
    }

    // ==========================================
    // AUTH SCREEN
    // ==========================================
    private fun showAuthScreen() {
        floatingNavBar.visibility = View.GONE
        contentFrame.removeAllViews()

        val view = LayoutInflater.from(this).inflate(R.layout.view_auth, contentFrame, false)

        val inputEndpoint = view.findViewById<EditText>(R.id.input_endpoint)
        val inputEmail = view.findViewById<EditText>(R.id.input_email)
        val inputPassword = view.findViewById<EditText>(R.id.input_password)
        val btnContinue = view.findViewById<LinearLayout>(R.id.btn_continue)
        val txtContinue = view.findViewById<TextView>(R.id.txt_continue)
        val btnDemoLogin = view.findViewById<LinearLayout>(R.id.btn_demo_login)

        inputEndpoint.setText(CargoResQApi.baseUrl)

        val doLogin = { email: String, pass: String ->
            CargoResQApi.baseUrl = inputEndpoint.text.toString().trim()
            txtContinue.text = "Signing in…"
            btnContinue.isEnabled = false

            executor.execute {
                val result = CargoResQApi.login(email.trim(), pass)
                mainHandler.post {
                    btnContinue.isEnabled = true
                    txtContinue.text = "Sign in securely"
                    result.onSuccess { session ->
                        currentSession = session
                        floatingNavBar.visibility = View.VISIBLE
                        switchTab(0)
                    }.onFailure { err ->
                        Toast.makeText(this, err.message ?: "Authentication failed", Toast.LENGTH_SHORT).show()
                    }
                }
            }
        }

        btnContinue.setOnClickListener {
            val email = inputEmail.text.toString()
            val pass = inputPassword.text.toString()
            if (email.isNotEmpty() && pass.isNotEmpty()) {
                doLogin(email, pass)
            } else {
                Toast.makeText(this, "Enter driver email and password", Toast.LENGTH_SHORT).show()
            }
        }

        btnDemoLogin.setOnClickListener {
            inputEmail.setText("rajesh@apexpharma.com")
            inputPassword.setText("Password123!")
            doLogin("rajesh@apexpharma.com", "Password123!")
        }

        contentFrame.addView(view)
    }

    // ==========================================
    // 1. HOME SCREEN (1:1 UBER DISPATCH & MAP)
    // ==========================================
    private fun renderHomeScreen(inflater: LayoutInflater) {
        val view = inflater.inflate(R.layout.view_home, contentFrame, false)

        val txtGreeting = view.findViewById<TextView>(R.id.txt_driver_greeting)
        val txtCarrier = view.findViewById<TextView>(R.id.txt_carrier_sub)
        val badgeStatus = view.findViewById<TextView>(R.id.badge_status)
        val txtReefer = view.findViewById<TextView>(R.id.txt_current_reefer)

        val btnSos = view.findViewById<LinearLayout>(R.id.btn_action_sos)
        val btnReefer = view.findViewById<LinearLayout>(R.id.btn_action_reefer)
        val btnPallet = view.findViewById<LinearLayout>(R.id.btn_action_pallet)
        val btnHubs = view.findViewById<LinearLayout>(R.id.btn_action_hubs)
        val btnTriggerBreakdown = view.findViewById<LinearLayout>(R.id.btn_trigger_breakdown)

        currentSession?.let {
            txtGreeting.text = it.driverName
            txtCarrier.text = "Apex Cold Logistics • 4.96 ★"
        }

        val triggerSosAction = {
            AlertDialog.Builder(this)
                .setTitle("🚨 Report Breakdown SOS?")
                .setMessage("CargoResQ will broadcast your emergency GPS position (NH-48 Km 84.2) to dispatch and lock nearest rescue reefer.")
                .setPositiveButton("Report Emergency") { _, _ ->
                    badgeStatus.text = "BREAKDOWN REPORTED"
                    badgeStatus.setBackgroundResource(R.drawable.bg_pill_danger)
                    badgeStatus.setTextColor(ContextCompat.getColor(this, R.color.uber_white))
                    txtReefer.setTextColor(ContextCompat.getColor(this, R.color.uber_red))

                    executor.execute {
                        currentSession?.let { s ->
                            CargoResQApi.reportBreakdown(s.token, 18.7511, 73.3422)
                        }
                    }

                    Toast.makeText(this, "SOS Sent. Rescue unit TRK-9042 dispatched.", Toast.LENGTH_LONG).show()
                }
                .setNegativeButton("Cancel", null)
                .show()
        }

        btnSos.setOnClickListener { triggerSosAction() }
        btnTriggerBreakdown.setOnClickListener { triggerSosAction() }

        btnPallet.setOnClickListener {
            AlertDialog.Builder(this)
                .setTitle("📦 Pallet Handover QR Verification")
                .setMessage("Shipment ID: SHP-8492\nCargo: Vaccines & Insulin (1,450 kg)\nCold Integrity: VERIFIED (4.1°C)\nDigital Seal: MATCHED (SHA-256 Validated)\n\nConfirm physical custody release to rescue vehicle?")
                .setPositiveButton("Confirm Handover") { _, _ ->
                    badgeStatus.text = "ESCROW RELEASED"
                    badgeStatus.setBackgroundResource(R.drawable.bg_pill_demo)
                    badgeStatus.setTextColor(ContextCompat.getColor(this, R.color.uber_teal))
                    Toast.makeText(this, "Pallet transfer verified. Smart contract released.", Toast.LENGTH_LONG).show()
                }
                .setNegativeButton("Cancel", null)
                .show()
        }

        btnReefer.setOnClickListener {
            AlertDialog.Builder(this)
                .setTitle("❄️ Cold Chain Telemetry")
                .setMessage("Cargo Chamber: +4.1°C (Nominal)\nSetpoint Band: +2.0°C to +8.0°C\nAmbient Outdoor: +34.8°C\nCompressor Duty: 92%\nBattery Backup: 98%\nIoT Link: Nominal LTE")
                .setPositiveButton("Done", null)
                .show()
        }

        btnHubs.setOnClickListener {
            AlertDialog.Builder(this)
                .setTitle("🏢 Nearest Cold Storage Network")
                .setMessage("1. Pune Cold Logistics Hub (14.2 km)\n2. Panvel Transshipment Terminal (28.6 km)\n3. Khopoli Expressway Reefer Dock (8.1 km)")
                .setPositiveButton("Close", null)
                .show()
        }

        contentFrame.addView(view)
    }

    // ==========================================
    // 2. SERVICES SCREEN (1:1 UBER SERVICES)
    // ==========================================
    private fun renderServicesScreen(inflater: LayoutInflater) {
        val view = inflater.inflate(R.layout.view_services, contentFrame, false)

        val inputDist = view.findViewById<EditText>(R.id.input_distance)
        val btnReefer = view.findViewById<LinearLayout>(R.id.btn_veh_reefer)
        val btnAce = view.findViewById<LinearLayout>(R.id.btn_veh_ace)
        val btnBolero = view.findViewById<LinearLayout>(R.id.btn_veh_bolero)

        val txtFareReefer = view.findViewById<TextView>(R.id.txt_fare_reefer)
        val txtFareAce = view.findViewById<TextView>(R.id.txt_fare_ace)
        val txtFareBolero = view.findViewById<TextView>(R.id.txt_fare_bolero)

        val calculateFares = {
            val dist = inputDist.text.toString().toDoubleOrNull() ?: 85.0
            val reeferFare = (1200 + dist * 40).toInt()
            val aceFare = (600 + dist * 15).toInt()
            val boleroFare = (800 + dist * 20).toInt()

            txtFareReefer.text = "₹$reeferFare"
            txtFareAce.text = "₹$aceFare"
            txtFareBolero.text = "₹$boleroFare"
        }

        inputDist.addTextChangedListener(object : android.text.TextWatcher {
            override fun beforeTextChanged(s: CharSequence?, start: Int, count: Int, after: Int) {}
            override fun onTextChanged(s: CharSequence?, start: Int, before: Int, count: Int) {
                calculateFares()
            }
            override fun afterTextChanged(s: android.text.Editable?) {}
        })

        btnReefer.setOnClickListener {
            btnReefer.setBackgroundResource(R.drawable.bg_pill_button)
            btnAce.setBackgroundResource(R.drawable.bg_pill_demo)
            btnBolero.setBackgroundResource(R.drawable.bg_pill_demo)
            Toast.makeText(this, "Selected: Tata Ultra Reefer (-20°C)", Toast.LENGTH_SHORT).show()
        }

        btnAce.setOnClickListener {
            btnAce.setBackgroundResource(R.drawable.bg_pill_button)
            btnReefer.setBackgroundResource(R.drawable.bg_pill_demo)
            btnBolero.setBackgroundResource(R.drawable.bg_pill_demo)
            Toast.makeText(this, "Selected: Tata Ace (750 kg)", Toast.LENGTH_SHORT).show()
        }

        btnBolero.setOnClickListener {
            btnBolero.setBackgroundResource(R.drawable.bg_pill_button)
            btnReefer.setBackgroundResource(R.drawable.bg_pill_demo)
            btnAce.setBackgroundResource(R.drawable.bg_pill_demo)
            Toast.makeText(this, "Selected: Bolero Maxi (1.2 T)", Toast.LENGTH_SHORT).show()
        }

        view.findViewById<LinearLayout>(R.id.card_service_reefer).setOnClickListener {
            Toast.makeText(this, "Service: Cold-chain Reefer Rescue (Active)", Toast.LENGTH_SHORT).show()
        }
        view.findViewById<LinearLayout>(R.id.card_service_hauler).setOnClickListener {
            Toast.makeText(this, "Service: Heavy Breakdown Hauler (Available)", Toast.LENGTH_SHORT).show()
        }
        view.findViewById<LinearLayout>(R.id.card_service_hazmat).setOnClickListener {
            Toast.makeText(this, "Service: Hazmat ADR Certified Recovery", Toast.LENGTH_SHORT).show()
        }
        view.findViewById<LinearLayout>(R.id.card_service_crossdock).setOnClickListener {
            Toast.makeText(this, "Service: Immediate Dock-to-Dock Transfer", Toast.LENGTH_SHORT).show()
        }

        contentFrame.addView(view)
    }

    // ==========================================
    // 3. ACTIVITY SCREEN (1:1 UBER ACTIVITY)
    // ==========================================
    private fun renderActivityScreen(inflater: LayoutInflater) {
        val view = inflater.inflate(R.layout.view_activity, contentFrame, false)

        val btnFilter = view.findViewById<LinearLayout>(R.id.btn_filter_activity)
        btnFilter.setOnClickListener {
            // 1:1 Uber Dark Activity Filter Bottom Sheet
            val bottomSheet = BottomSheetDialog(this)
            val container = LinearLayout(this).apply {
                orientation = LinearLayout.VERTICAL
                setPadding(48, 48, 48, 64)
                setBackgroundColor(ContextCompat.getColor(context, R.color.uber_pill_dark))

                val title = TextView(context).apply {
                    text = "Activity Filters"
                    setTextColor(ContextCompat.getColor(context, R.color.uber_white))
                    textSize = 20f
                    typeface = android.graphics.Typeface.DEFAULT_BOLD
                }
                addView(title)

                val opt1 = TextView(context).apply {
                    text = "✓ All Rescues & Deliveries"
                    setTextColor(ContextCompat.getColor(context, R.color.uber_teal_soft))
                    textSize = 14f
                    setPadding(0, 32, 0, 16)
                }
                addView(opt1)

                val opt2 = TextView(context).apply {
                    text = "Cold Chain Only (< 8°C)"
                    setTextColor(ContextCompat.getColor(context, R.color.uber_white))
                    textSize = 14f
                    setPadding(0, 16, 0, 16)
                }
                addView(opt2)

                val opt3 = TextView(context).apply {
                    text = "Escrow Released Trips"
                    setTextColor(ContextCompat.getColor(context, R.color.uber_white))
                    textSize = 14f
                    setPadding(0, 16, 0, 32)
                }
                addView(opt3)

                val closeBtn = Button(context).apply {
                    text = "Apply Filters"
                    setBackgroundResource(R.drawable.bg_pill_button)
                    setTextColor(ContextCompat.getColor(context, R.color.uber_white))
                    setOnClickListener { bottomSheet.dismiss() }
                }
                addView(closeBtn)
            }
            bottomSheet.setContentView(container)
            bottomSheet.show()
        }

        contentFrame.addView(view)
    }

    // ==========================================
    // 4. ACCOUNT SCREEN (1:1 UBER ACCOUNT)
    // ==========================================
    private fun renderAccountScreen(inflater: LayoutInflater) {
        val view = inflater.inflate(R.layout.view_account, contentFrame, false)

        val txtName = view.findViewById<TextView>(R.id.txt_account_name)
        val btnSignOut = view.findViewById<LinearLayout>(R.id.btn_sign_out)
        val btnSafety = view.findViewById<LinearLayout>(R.id.btn_action_safety)
        val btnWallet = view.findViewById<LinearLayout>(R.id.btn_action_wallet)
        val btnHelp = view.findViewById<LinearLayout>(R.id.btn_action_help)

        currentSession?.let {
            txtName.text = it.driverName
        }

        btnSafety.setOnClickListener {
            AlertDialog.Builder(this)
                .setTitle("🛡️ Safety Hub")
                .setMessage("GPS Live Tracking: Active\nAutomated Spoilage Sentinel: Nominal\nEscrow Insurance: Covered up to ₹25,00,000")
                .setPositiveButton("OK", null)
                .show()
        }

        btnWallet.setOnClickListener {
            AlertDialog.Builder(this)
                .setTitle("💳 Driver Earnings")
                .setMessage("Current Month: ₹1,24,800\nCompleted Rescues: 18\nOn-time SLA: 99.4%\nNext Payout: Friday")
                .setPositiveButton("Close", null)
                .show()
        }

        btnHelp.setOnClickListener {
            val intent = Intent(Intent.ACTION_DIAL, Uri.parse("tel:1033"))
            try {
                startActivity(intent)
            } catch (_: Exception) {
                Toast.makeText(this, "NHAI Helpline: 1033", Toast.LENGTH_LONG).show()
            }
        }

        btnSignOut.setOnClickListener {
            currentSession = null
            showAuthScreen()
        }

        contentFrame.addView(view)
    }
}
