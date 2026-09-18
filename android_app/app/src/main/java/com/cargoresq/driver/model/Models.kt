package com.cargoresq.driver.model

data class DriverSession(
    val token: String,
    val driverId: String,
    val driverName: String,
    val companyId: String,
    val email: String
)

data class ShipmentInfo(
    val id: String,
    val cargoType: String,
    val status: String,
    val requiresRefrigeration: Boolean,
    val requiredMaxTempC: Double?,
    val weightKg: Int,
    val valueInr: Long
)

data class IncidentInfo(
    val id: String,
    val shipmentId: String,
    val cargoType: String,
    val state: String,
    val minutesUntilSpoilage: Int?
)

data class ActivityItem(
    val id: String,
    val destination: String,
    val cargo: String,
    val date: String,
    val fareInr: Int,
    val status: String
)

data class VehicleOption(
    val name: String,
    val category: String,
    val capacity: String,
    val baseFare: Int,
    val perKm: Int,
    val isReefer: Boolean
)
