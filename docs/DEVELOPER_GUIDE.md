# CargoResQ — Developer & Contributor Guide

> **Repository**: [https://github.com/neuralsin/CargoResQ](https://github.com/neuralsin/CargoResQ)  
> **CI Status**: GitHub Actions Automated Pytest & Compile Check

This guide covers developer onboarding, local environment setup, testing standards, database migration patterns, and building desktop and mobile binaries.

---

## 1. Prerequisites & Tooling

- **Python**: Version `3.11.x` (Recommended: Python 3.11.9 or 3.11.16)
- **Node/Git**: Git with LFS support
- **Java/Android SDK**: JDK 17+ and Android SDK (Platform 34) for mobile app builds
- **Database**: SQLite (built-in development mode) or PostgreSQL 16 with PostGIS extension (production mode)

---

## 2. Environment Setup

### 2.1 Clone and Virtual Environment
```bash
git clone https://github.com/neuralsin/CargoResQ.git
cd CargoResQ

# Create and activate virtual environment
python -m venv .venv
# On Windows PowerShell:
.venv\Scripts\Activate.ps1
# On Linux / macOS:
source .venv/bin/activate
```

### 2.2 Dependency Installation
Install backend and testing dependencies:
```bash
python -m pip install --upgrade pip
pip install -r requirements.txt
```

> **Note**: `requirements.txt` includes `pydantic[email]>=2.6.0` and `email-validator>=2.0.0` required for RFC-compliant email validation on authentication models.

For Desktop client dependencies:
```bash
pip install -r desktop_app/requirements.txt
```

---

## 3. Running the Test Suite

CargoResQ features an automated end-to-end test suite (105+ tests) verifying security boundaries, Dijkstra pathfinding, two-way handshakes, WebSocket room isolation, and cold-chain escrow release logic.

Run the full suite with verbose logging:
```bash
python -m pytest -v
```

Run specific test modules:
```bash
# Run rescue lifecycle tests
python -m pytest tests/test_rescue_lifecycle.py -v

# Run escrow settlement tests
python -m pytest tests/test_escrow_ledger.py -v

# Run two-way offer handshake tests
python -m pytest tests/test_offer_handshake.py -v

# Run telemetry and SOS emergency tests
python -m pytest tests/test_sos_and_telemetry.py -v
```

To verify Python source compilation:
```bash
python -m compileall -q main.py shared services
```

---

## 4. Database Schema & Alembic Migrations

CargoResQ uses SQLAlchemy 2.0 with async engine and Alembic migrations.

### Applying Migrations
```bash
alembic upgrade head
```

### Creating New Migrations
When modifying models in `services/*/app/models.py` or `shared/schema.py`:
```bash
alembic revision --autogenerate -m "describe_schema_change"
```

### Seeding Enriched Multi-Carrier Demo Data
To populate 4 carriers, 9 trucks, 5 live shipments, real-time telemetry positions, and active rescue offers:
```bash
python scripts/seed_demo.py
```
Demo credentials are saved to `demo_credentials.json`.

---

## 5. Running the Application

### 5.1 Backend API Server
```bash
python main.py
```
Or via Uvicorn directly:
```bash
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```
Swagger UI is available at: [http://localhost:8000/docs](http://localhost:8000/docs)

### 5.2 Desktop Operations Console
Launch the CustomTkinter operations console:
```bash
python -m desktop_app.app
```
To compile a standalone Windows executable (`dist/CargoResQ/CargoResQ.exe`):
```powershell
./desktop_app/build_windows.ps1
```

### 5.3 Native Android Driver App
The Android app is located in `android_app/`:
```bash
cd android_app
./gradlew assembleDebug
```
The compiled APK will be located at:
`android_app/app/build/outputs/apk/debug/app-debug.apk`

#### Connecting a Real Android Phone over USB (ADB Reverse)
To connect your physical Android phone to your local dev backend:
1. Enable USB Debugging on your phone.
2. Plug phone into PC via USB cable.
3. Run:
   ```cmd
   connect_phone_usb.bat
   ```
4. On the CargoResQ Android app login screen, keep Server Address as `http://127.0.0.1:8000`.

---

## 6. Continuous Integration (CI)

Every commit to `main` and Pull Request triggers GitHub Actions:
- **Workflow**: `.github/workflows/ci.yml`
- **Environment**: Ubuntu Latest with Python 3.11
- **Steps**:
  1. `pip install -r requirements.txt`
  2. `python -m pytest -v` (Must achieve 100% pass rate)
  3. `python -m compileall -q main.py shared services`
