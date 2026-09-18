# CargoResQ — REST & WebSocket API Reference Specification

> **Base URL**: `http://localhost:8000` (Local) / `https://api.cargoresq.in` (Production)  
> **Interactive Swagger UI**: `http://localhost:8000/docs`  
> **Repository**: [https://github.com/neuralsin/CargoResQ](https://github.com/neuralsin/CargoResQ)

All endpoints expect JSON payloads with header `Content-Type: application/json`. Protected endpoints require an `Authorization: Bearer <token>` HTTP header.

---

## 1. Authentication & Identity (`/api/v1/auth`, `/api/v1/driver`)

### `POST /api/v1/auth/register`
Registers a new transport company/carrier on CargoResQ.
- **Request Body**:
  ```json
  {
    "name": "Reliable Cold Logistics Pvt Ltd",
    "email": "ops@reliablecold.in",
    "password": "StrongPassword123!"
  }
  ```
- **Response `201 Created`**:
  ```json
  {
    "access_token": "eyJhbGciOi...",
    "token_type": "bearer",
    "company_id": "0df294b1-..."
  }
  ```

### `POST /api/v1/auth/login`
Authenticates a company administrator and returns an access token.

### `POST /api/v1/driver/register`
Company admin creates a driver account under their carrier fleet.
- **Request Body**:
  ```json
  {
    "name": "Rajesh Kumar",
    "email": "rajesh.driver@reliablecold.in",
    "password": "DriverPassword123!",
    "assigned_truck_id": "843bb0de-..."
  }
  ```

### `POST /api/v1/driver/login`
Driver authentication endpoint. Returns a driver JWT scoped to their assigned truck.

---

## 2. Fleet & Shipments (`/api/v1/trucks`, `/api/v1/shipments`)

### `GET /api/v1/trucks`
Lists all vehicles owned by the authenticated company.

### `POST /api/v1/trucks`
Registers a new vehicle in the carrier fleet.
- **Request Body**:
  ```json
  {
    "registration_number": "MH-12-RN-8821",
    "latitude": 18.5204,
    "longitude": 73.8567,
    "refrigerated": true,
    "min_temp_c": -20.0,
    "max_volume_m3": 28.0,
    "max_weight_kg": 9000.0
  }
  ```

### `POST /api/v1/shipments`
Registers an active consignment in transit.
- **Request Body**:
  ```json
  {
    "title": "Frozen Shrimp Export Consignment",
    "origin_address": "Ratnagiri Port, MH",
    "destination_address": "Nhava Sheva JNPT, MH",
    "cargo_type": "PERISHABLE_FROZEN",
    "target_temperature_c": -18.0,
    "max_temperature_c": -15.0,
    "spoilage_timeout_minutes": 180,
    "weight_kg": 4500.0,
    "volume_m3": 14.5
  }
  ```

---

## 3. Telemetry & Anomaly Ingest (`/api/v1/telemetry`)

### `POST /api/v1/telemetry/pings`
High-throughput micro-batch transponder and sensor ingestion endpoint.
- **Headers**: `Authorization: Bearer <driver_or_truck_token>`
- **Request Body**:
  ```json
  {
    "device_id": "tel-hw-8902",
    "pings": [
      {
        "client_ping_id": "550e8400-e29b-41d4-a716-446655440000",
        "recorded_at": "2026-09-18T20:15:30Z",
        "latitude": 18.6012,
        "longitude": 73.7821,
        "speed_kph": 52.4,
        "heading_deg": 135.0,
        "temp_c": -18.2,
        "is_mocked": false
      }
    ]
  }
  ```
- **Response `200 OK`**:
  ```json
  {
    "accepted": 1,
    "rejected": 0,
    "alerts_triggered": []
  }
  ```

### `GET /api/v1/telemetry/fleet/live`
Returns real-time locations and operational health for all vehicles across the fleet.

---

## 4. SOS Emergency Transponder (`/api/v1/sos`)

### `POST /api/v1/sos`
Raises an immediate emergency transponder alert.
- **Request Body**:
  ```json
  {
    "category": "BREAKDOWN",
    "severity": "CRITICAL",
    "latitude": 18.5204,
    "longitude": 73.8567,
    "notes": "Engine coolant burst on highway shoulder."
  }
  ```
- **Response `201 Created`**: Broadcasts `sos.raised` via WebSockets to the carrier's operations room and nearby candidate rescuers.

### `POST /api/v1/sos/{id}/cancel`
Cancels the emergency alert.
- **Request Body**:
  ```json
  {
    "pin": "1234"
  }
  ```
  *(Note: Submitting duress PIN `9999` sends a silent duress flag to law enforcement and dispatch while showing the UI as cancelled).*

---

## 5. Two-Way Offer Handshake (`/api/v1/offers`)

### `POST /api/v1/offers`
Dispatches a rescue quote between stranded carrier and candidate rescuer.
- **Request Body**:
  ```json
  {
    "incident_id": "08f3ba91-...",
    "helper_truck_id": "762c90df-...",
    "price_inr": 35000.0,
    "expires_in_minutes": 15
  }
  ```

### `POST /api/v1/offers/{id}/accept`
Called by the stranded carrier to accept the rescue quote.

### `POST /api/v1/offers/{id}/confirm`
Called by the rescuing helper to lock their truck and commit.
Once **both** parties have confirmed, the offer transitions atomically to `BOUND`, triggering automated escrow deposit locking.

---

## 6. Escrow Settlement Ledger (`/api/v1/escrow`)

### `GET /api/v1/escrow/{incident_id}`
Returns the double-entry escrow state, held amount, and verification status.

### `POST /api/v1/escrow/verify-temperature`
Automated IoT cold-chain attestation verification.
- **Request Body**:
  ```json
  {
    "incident_id": "08f3ba91-..."
  }
  ```
- If sensor telemetry confirms temperature never breached max tolerance, returns `verdict: PASS` and initiates automatic fund release.

---

## 7. Realtime WebSocket Gateway (`/ws`)

Connect to:
`ws://localhost:8000/ws`

### Protocol Flow:
1. Client establishes connection.
2. Client sends auth frame within 10s:
   ```json
   {"type": "auth", "token": "<JWT_ACCESS_TOKEN>"}
   ```
3. Server responds:
   ```json
   {"type": "auth.ok", "company_id": "0df294b1-..."}
   ```
4. Real-time events stream asynchronously:
   - `telemetry.location`: Truck GPS and speed movement.
   - `sos.raised` / `sos.cancelled`: Emergency transponder updates.
   - `offer.created` / `offer.bound`: Two-way handshake agreements.
   - `escrow.held` / `escrow.released`: Financial settlements.
