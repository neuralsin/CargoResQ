"""
Model registration.

Importing this module imports every mapped class exactly once, which is what
populates ``Base.metadata``. It exists so that no model module has to be
imported from ``shared.database`` -- doing that created an import cycle,
because every model module imports ``Base`` from there.

Import this from application entrypoints and from Alembic's env.py. Never
import it from a model module.
"""
from shared.database import Base  # noqa: F401  (re-exported for convenience)

from services.core_api.app.models import (  # noqa: F401
    Company,
    Driver,
    Shipment,
    Truck,
    TruckStatus,
)
from services.escrow_ledger.app.models import Escrow, LedgerEntry  # noqa: F401
from services.orchestrator.app.models import Incident, IncidentEvent  # noqa: F401
from services.orchestrator.app.offer_models import RescueOffer  # noqa: F401
from services.orchestrator.app.reputation_models import (  # noqa: F401
    CompanyReputation,
    RescueRating,
)
from services.sos.app.models import (  # noqa: F401
    EmergencyContact,
    SosAlert,
    SosBroadcast,
    SosEvent,
    SosResponse,
)
from services.telemetry.app.models import (  # noqa: F401
    CargoReading,
    ColdChainAttestation,
    ShipmentRoute,
    TelemetryAlert,
    TelemetryPing,
    TruckLiveState,
)
from shared.idempotency import ProcessedEvent  # noqa: F401

__all__ = [
    "Base",
    "CargoReading",
    "ColdChainAttestation",
    "CompanyReputation",
    "EmergencyContact",
    "RescueOffer",
    "RescueRating",
    "ShipmentRoute",
    "SosAlert",
    "SosBroadcast",
    "SosEvent",
    "SosResponse",
    "TelemetryAlert",
    "TelemetryPing",
    "TruckLiveState",
    "Company",
    "Driver",
    "Shipment",
    "Truck",
    "TruckStatus",
    "Escrow",
    "LedgerEntry",
    "Incident",
    "IncidentEvent",
    "ProcessedEvent",
]
