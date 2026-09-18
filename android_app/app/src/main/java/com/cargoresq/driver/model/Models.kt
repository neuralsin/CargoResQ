package com.cargoresq.driver.model

/**
 * Domain types mirroring the API's JSON.
 *
 * Nullable where the server may genuinely have nothing to say. A truck that
 * has never reported has no position, and modelling that as 0.0 would put it
 * in the Gulf of Guinea.
 */

data class DriverSession(
    val token: String,
    val driverId: String,
    val driverName: String,
    val companyId: String,
    val email: String,
)

data class DriverProfile(
    val id: String,
    val name: String,
    val email: String,
    val phone: String?,
    val companyId: String,
    val assignedTruckId: String?,
    val truck: TruckInfo?,
)

data class TruckInfo(
    val id: String,
    val registrationNumber: String,
    val latitude: Double?,
    val longitude: Double?,
    val status: String,
    val refrigerated: Boolean,
    val minTempC: Double?,
)

data class ShipmentInfo(
    val id: String,
    val cargoType: String,
    val status: String,
    val requiresRefrigeration: Boolean,
    val requiredMaxTempC: Double?,
    val isHazmat: Boolean,
    val volumeM3: Double,
    val weightKg: Double,
    /**
     * The declared value of the load. Shown to the driver carrying it, since
     * knowing the cargo is worth five lakh changes how carefully it is
     * handled -- but never sent to a *rescuing* carrier's driver.
     */
    val valueInr: Double,
)

data class IncidentInfo(
    val id: String,
    val shipmentId: String,
    val cargoType: String?,
    val state: String,
    val minutesUntilSpoilage: Double?,
)

/**
 * A rescue request offered to this driver's truck.
 *
 * Carries no price. The dispatcher sees the payout and decides; the driver
 * sees where to go and what is involved.
 */
data class OfferInfo(
    val id: String,
    val incidentId: String,
    val state: String,
    val etaMinutes: Double?,
    val distanceKm: Double?,
    val expiresAt: String?,
    val carrierAccepted: Boolean,
    val ownerConfirmed: Boolean,
    val bound: Boolean,
)

data class SosAlertInfo(
    val id: String,
    val category: String,
    val severity: String,
    val status: String,
    val psapDispatched: Boolean,
    val emergencyNumbers: List<EmergencyNumber>,
)

data class EmergencyNumber(
    val label: String,
    val number: String,
    val primary: Boolean,
)

data class EmergencyContact(
    val id: String,
    val name: String,
    val phone: String,
    val relationship: String?,
    val isPrimary: Boolean,
)

/** One queued GPS fix, awaiting upload. */
data class QueuedPing(
    val clientPingId: String,
    val recordedAt: String,
    val latitude: Double,
    val longitude: Double,
    val accuracyM: Float?,
    val speedKph: Double?,
    val headingDeg: Double?,
    val batteryPct: Double?,
    val isCharging: Boolean?,
    val mockLocation: Boolean,
)

data class TelemetryConfig(
    val pingIntervalSeconds: Int,
    val pingIntervalWhileStationarySeconds: Int,
    val batchSize: Int,
    val queueLimit: Int,
)

/** The driver's self-reported state, sent with an SOS. */
data class DriverCondition(
    val personsAffected: Int? = null,
    val isConscious: Boolean? = null,
    val isBreathing: Boolean? = null,
    val isTrapped: Boolean? = null,
    val isMobile: Boolean? = null,
    val severeBleeding: Boolean? = null,
    val note: String? = null,
)

enum class SosCategory(val wire: String, val label: String) {
    MEDICAL("MEDICAL", "Medical"),
    ACCIDENT("ACCIDENT", "Accident"),
    POLICE_SECURITY("POLICE_SECURITY", "Security"),
    FIRE("FIRE", "Fire"),
    MECHANICAL("MECHANICAL", "Breakdown"),
    CARGO_RISK("CARGO_RISK", "Cargo at risk"),
}

enum class SosSeverity(val wire: String, val label: String) {
    CRITICAL("CRITICAL", "Life threatening"),
    HIGH("HIGH", "Urgent"),
    MODERATE("MODERATE", "Needs help"),
}
