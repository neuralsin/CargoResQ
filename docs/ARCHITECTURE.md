# CargoResQ — System Architecture & Design Specification

> **Repository**: [https://github.com/neuralsin/CargoResQ](https://github.com/neuralsin/CargoResQ)  
> **Target Audience**: Core Engineers, DevOps, Distributed Systems Architects, and Security Reviewers.

---

## 1. Executive System Overview

**CargoResQ** is a high-availability, fault-tolerant platform designed to orchestrate emergency cross-carrier rescues for perishable and hazardous cargo stranded in transit across Indian logistics corridors (e.g., NH48 Delhi-Mumbai, NH44 North-South).

When a reefer truck carrying temperature-sensitive goods (e.g., seafood at -18°C, pharmaceuticals at 2–8°C) breaks down, a race against the **Spoilage Deadline** begins. Traditional logistics relies on manual phone calls, disjointed WhatsApp groups, and untrusted carrier handoffs. CargoResQ automates the entire lifecycle:
1. **Detection & SOS**: Automated IoT transponder telemetry ingest and 1-tap mobile driver SOS alerting.
2. **Matching & Scoring**: Multidimensional Dijkstra graph routing and spoilage-aware carrier ranking.
3. **Two-Way Binding**: Cryptographically sound, race-free two-way offer handshake enforced by PostgreSQL constraints.
4. **Conditional Escrow**: Double-entry ledger escrow that locks funds and releases them **only** upon verifiable cold-chain IoT temperature attestation at the delivery drop-off.
5. **Real-time Multiplexing**: WebSocket pub/sub rooms isolated by corporate tenant boundary.

---

## 2. High-Level Component Topology

```
+-----------------------------------------------------------------------------------+
|                                  CLIENT LAYER                                     |
|                                                                                   |
|   +------------------------------------+   +----------------------------------+   |
|   |    Desktop Operations Console      |   |    Android Driver Companion      |   |
|   |  (Python 3.11 / CustomTkinter /    |   |  (Native Kotlin / Retrofit /     |   |
|   |   OpenStreetMap live tile feed)    |   |   osmdroid / TelemetryService)   |   |
|   +-----------------+------------------+   +-----------------+----------------+   |
+---------------------|----------------------------------------|--------------------+
                      | HTTPS / WSS                            | HTTPS / WSS
                      v                                        v
+-----------------------------------------------------------------------------------+
|                              CARGORESQ CORE GATEWAY                               |
|                                                                                   |
|   +---------------------------------------------------------------------------+   |
|   | FastAPI Application Gateway (ASGI / Uvicorn)                              |   |
|   |   * Tenant Auth & Driver JWT Validation (shared.rbac)                     |   |
|   |   * Rate Limiting (SlowAPI)                                               |   |
|   |   * Prometheus Metrics & Structured JSON Logging                          |   |
|   +---------------------------------------------------------------------------+   |
+-----------------------------------------------------------------------------------+
       |                   |                   |                   |
       v                   v                   v                   v
+--------------+    +--------------+    +--------------+    +--------------+
|  TELEMETRY   |    | MATCHING &   |    | ORCHESTRATOR |    |    ESCROW    |
|   & SOS      |    |   ROUTING    |    |  & HANDSHAKE |    |    LEDGER    |
+--------------+    +--------------+    +--------------+    +--------------+
| - Ping Ingest|    | - Dijkstra   |    | - 2-Way Offer|    | - Double     |
| - GPS Jitter |    |   Shortest   |    |   State Mach.|    |   Entry DB   |
| - Temp Drift |    |   Path       |    | - Deadlocks  |    | - Cold-chain |
| - Duress PIN |    | - Spoilage   |    |   Prevention |    |   Attest.    |
| - Emergency  |    |   Classifier |    | - Audit Log  |    | - Auto Hold/ |
|   Broadcast  |    | - Dynamic    |    |   Projection |    |   Dispute    |
|              |    |   Pricing    |    |              |    |              |
+--------------+    +--------------+    +--------------+    +--------------+
       |                   |                   |                   |
       +-------------------+---------+---------+-------------------+
                                     |
                                     v
+-----------------------------------------------------------------------------------+
|                              DATA & EVENT PERSISTENCE                             |
|                                                                                   |
|   +----------------------------------+   +------------------------------------+   |
|   | PostgreSQL 16 + PostGIS          |   | Realtime Broadcast Hub             |   |
|   | (Fallback: SQLite + aiosqlite    |   | (In-Memory Room Multiplexer with   |   |
|   |  with GeoPoly Spatial Math)      |   |  Redis / Kafka Adapter Hooks)      |   |
|   +----------------------------------+   +------------------------------------+   |
+-----------------------------------------------------------------------------------+
```

---

## 3. Core Subsystems & Domain Design

### 3.1 Telemetry & SOS Transponder (`services/telemetry`, `services/sos`)
- **Ingest Pipeline**: High-frequency GPS pings and BLE temperature sensor payloads sent in micro-batches to `/api/v1/telemetry/pings`.
- **Anti-Spoofing & Jitter Filtering**: Algorithms reject false satellite drift, teleportation artifacts, and flag mock locations while maintaining tracking fidelity.
- **Dwell & Anomaly Detection**: Unplanned stops (>15 minutes on isolated highway sectors) automatically raise transponder warnings.
- **Safety & Duress System**: Drivers can enter a duress cancellation PIN which silently alerts dispatch while indicating to attackers that the alarm was cancelled.

### 3.2 Dijkstra Engine & Spoilage Optimization (`services/matching_engine`)
- **Network Graph**: Models Indian arterial transit nodes and highway segments with real-time transit times and toll costs.
- **Multi-Factor Scoring**:
  $$\text{Score} = w_1 \cdot \text{Proximity} + w_2 \cdot \text{CapacityFit} + w_3 \cdot \text{ColdChainCompliance} + w_4 \cdot \text{Reputation} + w_5 \cdot \text{SpoilageMargin}$$
- **Spoilage Margin Classification**:
  - `CRITICAL`: ETA within 30 minutes of thermal runaway $\rightarrow$ Instant auto-dispatch notification.
  - `ELEVATED`: ETA within 2 hours of thermal runaway.
  - `NOMINAL`: Ample safety buffer.

### 3.3 Two-Way Offer Handshake (`services/orchestrator`)
A rescue agreement is a legally binding mutual commitment:
- The **Stranded Carrier (Owner)** must accept the quote and carrier identity.
- The **Rescuing Carrier (Helper)** must confirm vehicle readiness and driver dispatch.
- **Race-Condition Defense**: Handled at the database level via partial unique indexes (`active_bound_rescue_per_incident_idx`). Even under high-concurrency requests, only one offer can ever transition to `BOUND` state; sibling offers are instantaneously auto-cancelled.

### 3.4 Cryptographic Escrow Ledger (`services/escrow_ledger`)
- **Strict Double-Entry Bookkeeping**: Accounts maintain zero-sum equilibrium (`DEBIT_ESCROW_HOLD = CREDIT_CARRIER_PAYABLE + CREDIT_PLATFORM_FEE`).
- **Cold-Chain Verifiable Settlement**:
  - `PASS`: Temperature never exceeded contract threshold $\rightarrow$ Payout automatically released to rescuer.
  - `FAIL`: Temperature spiked above max tolerance $\rightarrow$ Escrow transitioned to `DISPUTED`, funds frozen, incident escalated for arbitration.

---

## 4. WebSocket Room Isolation & Security

All WebSocket connections to `/ws` require a cryptographic JWT handshake frame within 5 seconds of opening:
```json
{
  "type": "auth",
  "token": "<JWT_BEARER_TOKEN>"
}
```
Once authenticated, the gateway subscribes the connection only to authorized rooms:
- `company:<company_id>`: Private operational events (vehicle pings, private offers, financial escrow releases).
- `network:alerts`: Redacted network alerts (anonymized breakdown coordinates, vehicle type requests).
- Cross-tenant data leakage is strictly blocked at the projection layer (`services/realtime_gateway/app/projections.py`).
