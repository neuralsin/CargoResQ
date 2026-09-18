# CargoResQ Driver — Native Android App

Native, production-ready Android companion application for field drivers on the **CargoResQ** emergency freight rescue network.

This application is **100% Android Studio compatible** and implements a **strict 1:1 Uber UI and UX system** modeled directly after [`abidiahmedcom/uber-ui-practice`](https://github.com/abidiahmedcom/uber-ui-practice), combining the sleek, high-contrast aesthetics of Uber with the emergency rescue capabilities of CargoResQ.

---

## Deliverables & Artifacts

| Deliverable | Location | Description |
| :--- | :--- | :--- |
| **Compiled Android APK** | `dist/CargoResQ-Driver.apk` | Ready-to-install standalone APK (5.85 MB) |
| **Android Studio Project** | `android_app/` | Complete standard Gradle project ready to open in Android Studio |
| **CustomTkinter Desktop Console** | `dist/CargoResQ.exe` | Native operations console for carrier owners (65.4 MB) |

---

## 1:1 Uber UI & UX Implementation

Adhering strictly to the visual tokens, layout patterns, and structure of `abidiahmedcom/uber-ui-practice`:

1. **Brand & Design Tokens**:
   - **Colors**: Uber Black (`#101615`), Pure White (`#FFFFFF`), Soft Mint Surface (`#E4F3EF`), Subtle Card Stroke (`#E5EAE7`), CargoResQ Teal (`#0F766E`), Emergency SOS Red (`#E11900`).
   - **Floating Pill Bottom Navigation**: Elevated dark pill (`#101615`) with smooth tab switching across **Home**, **Services**, **Activity**, and **Account**.

2. **Screen 1: Welcome & Auth Flow**:
   - Clean, high-contrast typography ("Move the rescue forward", "Driver Companion").
   - Country dial code pill (`🇮🇳 +91`) with formatted phone/email input.
   - One-Click **Demo Driver Login (Rajesh Kumar)** for instant presentation.
   - "Sign in securely" button with forward chevron.

3. **Screen 2: Home (Live Dispatch & Vector Map)**:
   - **Top Header**: Driver avatar pill, rating badge (`4.96 ★`), carrier tag (`Apex Cold Logistics`), and live beacon.
   - **"Where to?" Search Bar**: Re-engineered as **"Active Corridor: NH-48 Express (Mumbai → Pune)"**.
   - **Service Grid Circles**:
     - 🚨 **1-Touch SOS**: Instant breakdown emergency protocol with GPS broadcast.
     - ❄️ **4.1°C Reefer**: Live cold-chain telemetry monitor.
     - 📦 **Pallet QR**: Cryptographic custody seal handover.
     - 🏢 **Cold Hubs**: Nearest temperature-controlled transshipment facilities.
   - **Active Shipment Card**: Consignment `SHP-8492` (Insulin & Vaccines, 1,450 kg, ₹8,50,000 value, 2°C–8°C safe band).
   - **Uber Vector Map Card**: Styled sage green canvas with glowing route polyline, vehicle pulse, and signature floating **14 MIN TRIP • REEFER: +4.2°C** overlay badge.

4. **Screen 3: Services (Fleet & Porter Fare Estimator)**:
   - 2x2 Services Grid: Reefer Rescue, Heavy Hauler, Hazmat Certified, Cross-Dock.
   - **Porter-style Fleet Fare Estimator**: Dynamic distance calculation updating fares in real time for Tata Ultra Reefer, Tata Ace, and Bolero Maxi Truck.

5. **Screen 4: Activity (Past Rescues & Lifecycle)**:
   - Historical and in-flight rescue consignments with status badges (`DELIVERED`, `IN TRANSIT`, `ESCROW PAID`).
   - **Activity Filter Bottom Sheet**: High-contrast dark modal (1:1 with `activite-filtre-screen.jpg`) allowing drivers to filter by Cold Chain (< 8°C) or Escrow Released trips.

6. **Screen 5: Account (Driver Profile & Safety)**:
   - Profile header with rating badge (`★ 4.96 Verified Carrier Driver`).
   - 3-Action Quick Grid: **Safety Hub** (GPS beacon, Spoilage Sentinel), **Earnings** (₹1,24,800 monthly payout), and **Help** (1-tap dial NHAI Helpline 1033).
   - Assigned Truck card: `MH-12-TX-9042` (Tata Ultra Reefer, -20.0°C certified).

---

## Opening in Android Studio

1. Launch **Android Studio**.
2. Select **File → Open...**
3. Browse to the workspace directory and select the `android_app` folder.
4. Click **OK**. Android Studio will recognize the Gradle project, sync dependencies, and configure run configurations automatically.
5. Click **Run 'app'** (or press `Shift + F10`) to deploy to an attached Android device or Android Emulator.

---

## Building from the Command Line

```powershell
# Navigate to the Android project
cd android_app

# Build the debug APK
.\gradlew.bat assembleDebug

# The generated APK is at:
# android_app/app/build/outputs/apk/debug/app-debug.apk
# And mirrored to:
# dist/CargoResQ-Driver.apk
```

---

## Zero-Config Offline & Online Compatibility

The application is built with automatic API failover:
- When connected to the CargoResQ FastAPI server (default `http://10.0.2.2:8000` on Android emulator or local machine IP on physical devices), it communicates with the live backend endpoints (`/api/v1/driver/login`, `/me`, `/active-shipment`, `/report-breakdown`).
- When offline or during disconnected pitch demos, it seamlessly falls back to pre-seeded carrier telemetry without crashing, ensuring an uninterrupted presentation experience.
