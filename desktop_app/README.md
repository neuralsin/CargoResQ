# CargoResQ Desktop Operations Console

> **Main Project Documentation**: [README.md](../README.md)  
> **Repository**: [https://github.com/neuralsin/CargoResQ](https://github.com/neuralsin/CargoResQ)

The **CargoResQ Desktop Operations Console** is an operations command centre built for fleet dispatchers, logistics managers, and emergency response coordinators. Built natively using Python 3.11 and **CustomTkinter**, it provides real-time fleet visibility, interactive OpenStreetMap radar tracking, instant rescue offer negotiation, emergency SOS alerts, and escrow settlement visibility.

---

## 📸 Interface Preview

| Command Centre & Live Fleet Map | Emergency SOS Alert Panel |
|:---:|:---:|
| ![Command Centre](../screenshots/desktop_02_command_centre.png) | ![SOS Emergency](../screenshots/desktop_06_sos_emergency.png) |

---

## ✨ Features

- **Dark-Themed Modern UI**: High-contrast, clean aesthetic engineered for control-room operations.
- **Interactive Live Map Feed**: Rendered using `tkintermapview` with real-time OpenStreetMap tiles showing vehicle GPS pins, direction headings, and breakdown pins.
- **Dynamic Rescue Offers & Handshake**: Inspect incoming/outgoing rescue quotes, view carrier reputation scores, and bind two-way rescue commitments.
- **Emergency Transponder Beacon**: Instant visual takeover when an owned truck triggers an emergency SOS, showing GPS location, emergency category, and nearest candidate helpers.
- **Porter Fare Estimator Modal**: Built-in pricing estimator to evaluate route distance, freight base rate, and urgency premiums.
- **Escrow Settlement Dashboard**: Double-entry ledger balances, held funds, and verifiable cold-chain temperature attestation status.

---

## 🚀 Installation & Running

### 1. Install Dependencies
```powershell
python -m pip install -r desktop_app/requirements.txt
```

### 2. Launch the Application
```powershell
python -m desktop_app.app
```

### 3. Environment Variables
- `CARGOESQ_API_URL`: Base URL of the CargoResQ API (Default: `http://localhost:8000`).
- `CARGOESQ_WS_URL`: WebSocket URL (Default: `ws://localhost:8000/ws`).

---

## 📦 Building Standalone Windows Executable

To compile a standalone, zero-dependency Windows `.exe`:

```powershell
./desktop_app/build_windows.ps1
```

The compiled binary will be placed at:
```
dist/CargoResQ/CargoResQ.exe
```
