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
    /**
     * Exchanged for a new access token when the short one expires.
     *
     * Without this a driver was signed out roughly two hours into a shift and
     * every call started failing with "unauthorised" until they typed their
     * password again -- usually while stopped at the side of a road, which is
     * exactly when the app has to work.
     */
    val refreshToken: String,
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
 * What standing a breakdown down actually undid.
 *
 * Reported back rather than swallowed, because "cancelled" and "cancelled,
 * and two carriers who were holding a truck for you have been told" are
 * different things and the driver should see which one happened.
 */
data class StandDownResult(
    val incidentId: String,
    val state: String,
    val offersWithdrawn: Int,
    val escrowsReversed: Int,
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

/**
 * The other truck in this rescue, and how far away it is.
 *
 * A bound rescue has two drivers who each need exactly one thing about the
 * other: where they are and how long until they meet. The stranded driver is
 * stood beside a warming load wondering whether help is real; the rescuer is
 * hunting for a stopped truck somewhere ahead in the dark.
 *
 * Carries no price and no cargo value. A driver is told what they are moving,
 * never what it is worth or what the job pays.
 */
data class CounterpartLink(
    val myRole: String,
    val incidentId: String,
    val incidentState: String,
    val companyName: String,
    val driverName: String?,
    val driverPhone: String?,
    val registrationNumber: String?,
    val refrigerated: Boolean,
    val latitude: Double?,
    val longitude: Double?,
    val speedKph: Double?,
    val positionIsLive: Boolean,
    val lastSeenAt: String?,
    val distanceKm: Double?,
    val etaMinutes: Double?,
    val arrived: Boolean,
    val cargoType: String?,
    val minutesUntilSpoilage: Double?,
) {
    /** True when I am the one who broke down. */
    val iAmStranded: Boolean get() = myRole == "STRANDED"
}
