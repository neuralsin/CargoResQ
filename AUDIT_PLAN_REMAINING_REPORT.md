# CargoResQ — Audit Report: Remaining Items from Implementation Plan

**Audit Date**: September 18, 2026  
**Target Plan**: `CargoResQ — make it real` (Phases A through F)  
**Overall Completion**: **~85% Completed** (Core security, domain models, handshake, live maps, telemetry, and test suite are functional; 97/97 tests passing).

---

## Executive Summary

A comprehensive line-by-line audit of the current codebase against the **"CargoResQ — make it real"** implementation plan reveals that the vast majority of the critical structural, architectural, and security rework is **already implemented and operational**:

* **Security Holes Closed**: Server-enforced roles, token-scoped incident access, party-scoped escrow access with server-recorded temperature verification, authenticated room-scoped WebSockets, and explicit demo seeding are in place.
* **New Core Domain Implemented**: `rescue_offers` (two-way handshake with DB partial unique constraint), `telemetry_pings` / `truck_live_state`, `cargo_readings` / `cold_chain_attestations`, `telemetry_alerts`, `sos_alerts`, and `company_reputation` tables are live.
* **Both Clients Upgraded**:
  * The desktop console has real OpenStreetMap tiles via `tkintermapview`, live WebSocket updates, command centre, alerts, and SOS panels.
  * The Android driver app has been rewritten in native Kotlin with genuine error handling, `osmdroid` vector map, live job offers, safety guidance, one-touch Indian emergency dialer (112/108/100/101/1033), and a persistent `TelemetryService` foreground worker.
* **Test Suite**: 97 unit and integration tests passing on an isolated SQLite database fixture.

However, several specific tasks, cleanups, and edge-case wirings from Phases C, D, E, and F remain incomplete.

---

## Detailed Status Matrix by Phase

| Phase | Core Goal | Status | Key Remaining Work |
| :--- | :--- | :---: | :--- |
| **Phase A** | Security & Foundation | **95% DONE** | Untrack `android_app/local.properties` from Git. |
| **Phase B** | Missing Domain & Handshake | **90% DONE** | Automatic background subscriber chaining `breakdown.detected` → matching → offer fanout. |
| **Phase C** | Matching, Routing, Reputation | **70% DONE** | Replace fake `/fulfilment-rating`; rewrite `simulation.py` to drive real DB rows. |
| **Phase D** | Desktop Owner Console | **85% DONE** | Fix `CargoResQ.spec` hiddenimports (`tkintermapview`); fix documentation typos. |
| **Phase E** | Android Driver App | **85% DONE** | Rebuild real Trip History screen; configure release keystore / AAB. |
| **Phase F** | Tests, Demo, Docs | **60% DONE** | Create `scripts/run_demo.py`; write root `README.md` & update `DEPLOYMENT.md`. |

---

## Itemized Audit of Remaining Work

### 1. Phase A — Security & Foundation

- [x] **Single settings object**: Unified via [`services/core_api/app/config.py`](file:///c:/Users/shaan/OneDrive/Documents/C++/CargoResQ/services/core_api/app/config.py).
- [x] **Server-assigned roles**: Dropped client-provided `role` in registration; default `CARRIER_OWNER`.
- [x] **Guarded routes & tenant scoping**: All state-changing endpoints require token and verify company ownership.
- [x] **Escrow lockdown**: Removed public transition and verify endpoints; releases depend on database-recorded temperature logs.
- [x] **Scoped WebSockets**: Authenticated via frame on connection; scoped to company/driver rooms.
- [x] **Explicit seed script**: Boot-time seeding removed; [`scripts/seed_demo.py`](file:///c:/Users/shaan/OneDrive/Documents/C++/CargoResQ/scripts/seed_demo.py) handles deterministic creation.
- [x] **Cleaned legacy artifacts**: Deleted `static/` HTML files, fake `renders/`, unused `mobile/` React Native tree, and standalone service `Dockerfile`s.
- [ ] **Untrack `local.properties`**:
  * **Current State**: [`android_app/local.properties`](file:///c:/Users/shaan/OneDrive/Documents/C++/CargoResQ/android_app/local.properties) is still tracked in the Git repository.
  * **Remaining Action**: Run `git rm --cached android_app/local.properties` and add `local.properties` to [`android_app/.gitignore`](file:///c:/Users/shaan/OneDrive/Documents/C++/CargoResQ/android_app/.gitignore).

---

### 2. Phase B — Domain Models & Handshake Wiring

- [x] **`rescue_offers` Table**: Enforces 2-way handshake with partial unique indices (`uq_offer_bound_per_incident`, `uq_offer_owner_confirmed_per_incident`).
- [x] **Rescue Pricing & Policy**: Persists total INR, rescuer payout, and platform fee on each offer. Bounded multi-factor formula implemented in [`services/pricing_engine/app/pricing.py`](file:///c:/Users/shaan/OneDrive/Documents/C++/CargoResQ/services/pricing_engine/app/pricing.py).
- [x] **Telemetry & Condition Tables**: `telemetry_pings`, `truck_live_state`, `cargo_readings`, `cold_chain_attestations`, and `telemetry_alerts` created and migrated.
- [x] **SOS Protocol**: `sos_alerts`, `sos_events`, `sos_responses`, and `emergency_contacts` implemented with category and condition payloads.
- [x] **Reputation & Ratings**: `rescue_ratings` and `company_reputation` models actively calculate trust scores and acceptance rates.
- [ ] **Automatic Event Chaining (`breakdown.detected` → matching → quoting → offer broadcast)**:
  * **Current State**: When a breakdown is reported, `breakdown.detected` is published and projected over WebSockets to company rooms. However, the background subscriber to automatically run triage, matching, quoting, and candidate fan-out without operator intervention is not registered in [`main.py`](file:///c:/Users/shaan/OneDrive/Documents/C++/CargoResQ/main.py). Currently, an operator must manually initiate "Find Rescuers" from the desktop app or API.
  * **Remaining Action**: Wire an in-process event listener or background task in [`main.py`](file:///c:/Users/shaan/OneDrive/Documents/C++/CargoResQ/main.py) lifespan that automatically calls `find_candidates()` and `create_offer()` when zero-touch rescue automation is desired.

---

### 3. Phase C — Matching, Routing & Reputation

- [x] **Dijkstra Cleanup**: Removed hardcoded 16-node NH-48/SF graph and showpiece endpoints (`/matching/dijkstra`, `/matching/corridor/nodes`). Shortest-path routing rewritten to dynamically build a graph over real entity positions.
- [x] **Dynamic Scoring**: Rescue score weights use real bearing alignment, live truck coordinates, company trust scores, and historical acceptance rates.
- [ ] **Replace or Remove `/fulfilment-rating`**:
  * **Current State**: [`services/core_api/app/api/companies.py:64-96`](file:///c:/Users/shaan/OneDrive/Documents/C++/CargoResQ/services/core_api/app/api/companies.py#L64-L96) still returns hardcoded mock metrics:
    ```python
    "overallRating": 4.96,
    "completedTrips": 1240,
    "driverLeaderboard": [
        {"name": "Rajesh Kumar", "rating": 4.98, "transfers": 268},
        {"name": "Ryan K.", "rating": 4.90, "transfers": 184},
        ...
    ]
    ```
  * **Remaining Action**: Query real aggregates from `rescue_ratings`, `shipments`, and `company_reputation`, or delete the endpoint.
- [ ] **Rewrite `simulation.py` to Drive Real DB State**:
  * **Current State**: [`services/core_api/app/simulation.py`](file:///c:/Users/shaan/OneDrive/Documents/C++/CargoResQ/services/core_api/app/simulation.py) still uses a hardcoded `asyncio.sleep` timeline (`HAPPY_PATH_SCRIPT = [("incident.breakdown_reported", 0), ...]`) emitting mock event envelopes without inserting or updating real database rows in `incidents`, `rescue_offers`, or `escrows`.
  * **Remaining Action**: Refactor `run_simulation()` to create real database rows in `shipments`, `incidents`, and `offers`, executing the state machine transitions in fast-forward.
- [ ] **Verify PostGIS Generated Column on Postgres**:
  * **Current State**: The application runs and tests against SQLite using bounding-box + haversine filtering. The generated PostGIS column (`ST_SetSRID(ST_MakePoint(longitude, latitude), 4326)::geography`) has not been tested against a live PostgreSQL 16 + PostGIS 3.4 container.

---

### 4. Phase D — Desktop Owner Console (`desktop_app/`)

- [x] **Real Map**: Swapped canvas math for [`desktop_app/map_panel.py`](file:///c:/Users/shaan/OneDrive/Documents/C++/CargoResQ/desktop_app/map_panel.py) (`TkinterMapView` with cached OSM tiles, real markers, and polyline routes).
- [x] **Command Centre**: Candidate list displays rescue scores, breakdown reasons, rescuer reputation, and offer lifecycle.
- [x] **Telemetry & Alerts Tab**: Dwell, route deviation, GPS staleness, and temperature excursion alerts display live.
- [x] **SOS Console**: Inbound driver SOS and nearby network emergencies display with acknowledge and route actions.
- [x] **Live WebSocket Connection**: Implemented via [`desktop_app/live_feed.py`](file:///c:/Users/shaan/OneDrive/Documents/C++/CargoResQ/desktop_app/live_feed.py).
- [x] **Audit Trail & Escrow Dialogs**: Timeline modal displays real append-only `incident_events` and SLA metrics.
- [ ] **PyInstaller Spec (`CargoResQ.spec`) Dependencies**:
  * **Current State**: [`desktop_app/CargoResQ.spec:14`](file:///c:/Users/shaan/OneDrive/Documents/C++/CargoResQ/desktop_app/CargoResQ.spec#L14) only lists `hiddenimports=["customtkinter", "api_client"]`.
  * **Problem**: `tkintermapview` requires `geopy`, `geocoder`, `pyperclip`, `certifi`, and `pywin32`. A frozen binary compiled with the current spec will fail on map initialization.
  * **Remaining Action**: Add `collect_data_files("certifi")` and the missing packages to `hiddenimports` in `CargoResQ.spec`.
- [ ] **Documentation Typo Cleanup**:
  * **Current State**: `CARGOESQ_API_URL` (missing the 'R') is still referenced in [`DEPLOYMENT.md:5`](file:///c:/Users/shaan/OneDrive/Documents/C++/CargoResQ/DEPLOYMENT.md#L5) and [`desktop_app/README.md:5`](file:///c:/Users/shaan/OneDrive/Documents/C++/CargoResQ/desktop_app/README.md#L5).
  * **Remaining Action**: Update references to `CARGORESQ_API_URL` while keeping the typo as a backwards-compatible fallback.

---

### 5. Phase E — Android Driver App (`android_app/`)

- [x] **Eliminated Silent Failures**: [`CargoResQApi.kt`](file:///c:/Users/shaan/OneDrive/Documents/C++/CargoResQ/android_app/app/src/main/java/com/cargoresq/driver/api/CargoResQApi.kt) propagates real errors to the driver UI.
- [x] **OSM Vector Map**: `osmdroid` is integrated with OpenStreetMap tile caching and GPS tracking.
- [x] **SOS Dispatcher**: Full category picker, vitals/condition reporting, and `ACTION_DIAL` intent to 112/108/100/101/1033.
- [x] **Safety Guidance & Contacts**: Cargo-aware Do's & Don'ts and company emergency contacts.
- [x] **Offer Inbox**: Real-time jobs list for incoming rescue bids with Accept/Reject actions.
- [x] **Foreground Telemetry Service**: [`TelemetryService.kt`](file:///c:/Users/shaan/OneDrive/Documents/C++/CargoResQ/android_app/app/src/main/java/com/cargoresq/driver/telemetry/TelemetryService.kt) streams GPS, battery, and condition data with an offline FIFO queue.
- [x] **Debug APK**: Successfully compiled and mirrored to [`dist/CargoResQ-Driver.apk`](file:///c:/Users/shaan/OneDrive/Documents/C++/CargoResQ/dist/CargoResQ-Driver.apk).
- [ ] **Activity / Trip History Screen**:
  * **Current State**: When the "Jobs" (Offer Inbox) and "Safety" tabs were introduced, `view_activity.xml` and `view_services.xml` were deleted. There is currently no screen in `MainActivity.kt` displaying a driver's completed past rescues / historical consignments from `/api/v1/driver/history`.
  * **Remaining Action**: Add an Activity/History view or sub-tab backed by a `RecyclerView` fetching completed shipments.
- [ ] **Release Signing Configuration**:
  * **Current State**: [`android_app/keystore.properties`](file:///c:/Users/shaan/OneDrive/Documents/C++/CargoResQ/android_app/keystore.properties) does not exist. While the debug build works for LAN demonstrations, the release build does not have a configured signing key or generated Android App Bundle (`.aab`).
  * **Remaining Action**: Generate release keystore, populate `keystore.properties`, and test `./gradlew bundleRelease`.

---

### 6. Phase F — Tests, Demo & Documentation

- [x] **Non-destructive Test Suite**: `tests/conftest.py` uses an isolated test DB; running `pytest` preserves `cargoresq.db`.
- [x] **Security and Handshake Tests**: Added comprehensive test cases in `test_security_boundaries.py`, `test_offer_handshake.py`, and `test_sos_and_telemetry.py` (97 tests passing).
- [ ] **`scripts/run_demo.py` Helper Script**:
  * **Current State**: The plan calls for `scripts/run_demo.py` to auto-detect the host machine's Wi-Fi LAN IP, print the exact URL for the Android phone, and launch the server on `0.0.0.0:8000`. Only [`scripts/seed_demo.py`](file:///c:/Users/shaan/OneDrive/Documents/C++/CargoResQ/scripts/seed_demo.py) currently exists in `scripts/`.
  * **Remaining Action**: Create `scripts/run_demo.py` to automate the local demo startup.
- [ ] **Root `README.md` & `DEPLOYMENT.md` Overhaul**:
  * **Current State**:
    * There is **no root `README.md`** file in the repository.
    * [`DEPLOYMENT.md`](file:///c:/Users/shaan/OneDrive/Documents/C++/CargoResQ/DEPLOYMENT.md) is obsolete: it still describes building an Expo/EAS React Native app (`mobile/`), running migrations inside `services/core_api`, and deploying `infra/k8s/core-api-deployment.yaml` (which does not exist).
  * **Remaining Action**:
    * Create a unified root `README.md` documenting the architecture, desktop app, and Android app.
    * Rewrite `DEPLOYMENT.md` to reflect the actual single-process FastAPI backend, root Alembic migrations, CustomTkinter packaging, and native Android Gradle builds.

---

## Action Checklist for Completion

To bring the project to 100% adherence to the plan:

1. **Git Cleanup**:
   ```bash
   git rm --cached android_app/local.properties
   ```
2. **PyInstaller Spec**:
   Add `collect_data_files("certifi")` and `"tkintermapview"`, `"geopy"`, `"geocoder"`, `"pyperclip"`, `"certifi"` to `hiddenimports` in `desktop_app/CargoResQ.spec`.
3. **Docs & Typos**:
   Correct `CARGOESQ_API_URL` to `CARGORESQ_API_URL` in `DEPLOYMENT.md` and `desktop_app/README.md`.
4. **Demo Helper**:
   Implement `scripts/run_demo.py` to display the machine's LAN IP and boot the FastAPI backend.
5. **Backend Cleanups**:
   - Delete or wire real database metrics into `GET /api/v1/companies/{id}/fulfilment-rating`.
   - Update `services/core_api/app/simulation.py` to drive real DB rows.
6. **Documentation**:
   Write a root `README.md` and modernize `DEPLOYMENT.md`.
