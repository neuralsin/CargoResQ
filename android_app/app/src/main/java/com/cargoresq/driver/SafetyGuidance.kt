package com.cargoresq.driver

import com.cargoresq.driver.model.ShipmentInfo

/**
 * What to do, and what not to do, when a truck stops on a highway.
 *
 * Cargo-aware, because the right advice for a reefer full of insulin is not
 * the right advice for a tanker of solvents, and generic safety text gets
 * scrolled past. The reefer guidance in particular is the difference between
 * a delayed load and a written-off one.
 *
 * Sourced from ordinary Indian highway practice (NHAI roadside procedure) and
 * standard cold-chain handling. It is guidance, not a substitute for the
 * operator's own written procedures.
 */
object SafetyGuidance {

    data class Guidance(val dos: List<String>, val donts: List<String>, val context: String)

    private val UNIVERSAL_DOS = listOf(
        "Pull as far off the carriageway as the shoulder allows, and switch on hazard lights.",
        "Put the reflective triangle 50-100 m behind the truck, further on a curve or a crest.",
        "Wear the hi-vis jacket before you step out, even in daylight.",
        "Stand off the carriageway, behind the barrier if there is one, never between vehicles.",
        "Report the breakdown in this app so dispatch and nearby help can find you.",
    )

    private val UNIVERSAL_DONTS = listOf(
        "Do not attempt repairs on the offside, with your back to traffic.",
        "Do not stand in front of or behind the truck where a following vehicle cannot see you.",
        "Do not accept help from someone who will not identify themselves, at night or alone.",
        "Do not leave the vehicle unattended with the cargo unlocked.",
    )

    private val REEFER_DOS = listOf(
        "Keep the reefer running. If the engine has failed, the cold chain is now on a clock.",
        "Keep the doors shut. Every opening costs cold you cannot get back.",
        "Record the temperature in this app now, and again every 15 minutes.",
        "Tell dispatch the temperature reading, not just that you have stopped.",
    )

    private val REEFER_DONTS = listOf(
        "Do not open the doors to inspect the load unless dispatch asks you to.",
        "Do not switch the reefer off to save fuel while waiting for help.",
        "Do not guess a temperature. An invented reading can release a payment that should have been disputed.",
    )

    private val HAZMAT_DOS = listOf(
        "Move upwind and uphill of the vehicle, and stay there.",
        "Keep the transport emergency card (TREM card) with you, not in the cab.",
        "Tell 112 exactly what the UN number on the placard is.",
        "Keep everyone at least 50 m back until the authorities arrive.",
    )

    private val HAZMAT_DONTS = listOf(
        "Do not use a phone, torch or any switch near a suspected leak or vapour.",
        "Do not attempt to contain or clean up a spill yourself.",
        "Do not let anyone smoke anywhere near the vehicle.",
        "Do not move the vehicle after a leak, even to clear the carriageway.",
    )

    private val HIGH_VALUE_DOS = listOf(
        "Stay with the vehicle where it is safe to do so, and keep the cab locked.",
        "Photograph the seal and the load area before any transfer begins.",
        "Verify the rescuing driver against the details in this app before handing anything over.",
    )

    fun forShipment(shipment: ShipmentInfo?): Guidance {
        if (shipment == null) {
            return Guidance(
                dos = UNIVERSAL_DOS,
                donts = UNIVERSAL_DONTS,
                context = "General highway breakdown procedure.",
            )
        }

        val dos = ArrayList(UNIVERSAL_DOS)
        val donts = ArrayList(UNIVERSAL_DONTS)
        val contexts = ArrayList<String>()

        if (shipment.isHazmat) {
            // Hazmat guidance goes first: it is the part that kills people.
            dos.addAll(0, HAZMAT_DOS)
            donts.addAll(0, HAZMAT_DONTS)
            contexts.add("hazardous goods")
        }
        if (shipment.requiresRefrigeration) {
            dos.addAll(if (shipment.isHazmat) dos.size else 0, REEFER_DOS)
            donts.addAll(REEFER_DONTS)
            val limit = shipment.requiredMaxTempC
            contexts.add(
                if (limit != null) "cold chain, keep below " + limit + "C" else "cold chain"
            )
        }
        if (shipment.valueInr >= 500_000) {
            dos.addAll(HIGH_VALUE_DOS)
            contexts.add("high-value load")
        }

        val context = if (contexts.isEmpty()) {
            "Carrying " + shipment.cargoType + "."
        } else {
            "Carrying " + shipment.cargoType + " (" + contexts.joinToString(", ") + ")."
        }

        return Guidance(dos, donts, context)
    }
}
