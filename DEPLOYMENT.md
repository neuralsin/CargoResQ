# CargoResQ — Production Deployment Runbook

> **Repository**: [https://github.com/neuralsin/CargoResQ](https://github.com/neuralsin/CargoResQ)

CargoResQ has two supported client modes:

- `desktop_app/`: Native CustomTkinter operations console packaged as a Windows executable connecting to `CARGOESQ_API_URL`.
- `android_app/`: Dedicated native Kotlin Android driver app compiled with Gradle to produce an APK / AAB. Features persistent background telemetry and emergency SOS transponder.

---

## 1. Backend API Release

1. **Infrastructure Requirements**:
   - PostgreSQL 16 with PostGIS extension enabled.
   - Redis (for distributed WebSocket room clustering).
   - Kafka cluster (optional, for cross-service event streaming).
   - Reverse proxy / Ingress with TLS termination (Nginx, Traefik, or AWS ALB).

2. **Required Environment Variables**:
   - `DATABASE_URL`: `postgresql+asyncpg://user:pass@host:5432/cargoresq`
   - `JWT_SECRET`: 32+ cryptographically random characters
   - `ALLOWED_ORIGINS`: Comma-separated list of permitted origins
   - `REDIS_URL`: `redis://redis-host:6379/0`
   - `ENVIRONMENT`: `production`

3. **Docker Container Build**:
   ```bash
   docker build -f services/core_api/Dockerfile -t cargoresq/core-api:<release-tag> .
   docker push cargoresq/core-api:<release-tag>
   ```

4. **Run Versioned Migrations**:
   Run database schema migrations before promoting traffic to new containers:
   ```bash
   alembic upgrade head
   ```

5. **Kubernetes Deployment**:
   Apply updated manifests from `infra/k8s/` after injecting production secrets via Kubernetes Secret / Vault.
   Never commit plain-text credentials or production secrets to source control.

---

## 2. Operational Safeguards & Monitoring

- **Health Checks**: Verify `/health`, `/docs`, `/metrics`, and Prometheus endpoints before routing public ingress.
- **WebSocket Gateway**: Protect `/ws` behind SSL (`wss://`) with sticky session routing or Redis-backed room pub/sub.
- **Continuous Integration**: Ensure all 105 automated unit and integration tests pass via GitHub Actions before merging PRs to `main`.
- **Database Backups**: Enable continuous WAL archiving and automated point-in-time recovery for PostgreSQL.
- **Log Aggregation**: Structured JSON logs are emitted via `structlog` to stdout, ready for ingestion into Datadog, Grafana Loki, or ELK Stack.

---

## 3. Client Release Checklist

### Android Driver App (`android_app/`)
- Set production API URL in `CargoResQApi.kt` (`https://api.cargoresq.in`).
- Sign the release APK / Android App Bundle (AAB) using a production keystore:
  ```bash
  cd android_app
  ./gradlew bundleRelease
  ```
- Configure Google Play Console listing, screenshots, privacy policy URL, and support contacts.

### Desktop Operations Console (`desktop_app/`)
- Compile production Windows binary using PyInstaller:
  ```powershell
  ./desktop_app/build_windows.ps1
  ```
- Sign `CargoResQ.exe` with an EV Code Signing certificate.
- Package with Inno Setup or WiX for Windows installer distribution.
