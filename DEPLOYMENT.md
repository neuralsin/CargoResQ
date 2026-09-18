# CargoResQ deployment runbook

CargoResQ has two supported client modes:

- `desktop_app/` is a native CustomTkinter operations console. It is packaged as a Windows executable and connects to the API origin configured by `CARGOESQ_API_URL`.
- `mobile/` is the dedicated Android driver app. It uses EAS to produce an Android App Bundle and sends driver GPS only when the driver reports a breakdown.

## API release

1. Provision PostgreSQL 16 with PostGIS, Redis, Kafka (or the configured event provider), object storage, TLS, and a secret manager.
2. Set `DATABASE_URL`, `JWT_SECRET` (32+ random characters), `ALLOWED_ORIGINS`, `REDIS_URL`, `KAFKA_BOOTSTRAP_SERVERS`, and `OSRM_BASE_URL` in the deployment environment.
3. Build and tag the root-context image:

   ```bash
   docker build -f services/core_api/Dockerfile -t cargoresq/core-api:<release> .
   docker push cargoresq/core-api:<release>
   ```

4. Run the versioned migrations before starting new pods:

   ```bash
   cd services/core_api && alembic upgrade head
   ```

5. Deploy `infra/k8s/core-api-deployment.yaml` after replacing the image tag and applying a real Secret. Do not commit credentials or use the development fallback secret in production.
6. Verify `/health`, `/docs`, `/metrics`, company login, driver login, and a test SOS in staging before promoting the image.

## Operational safeguards

- Keep the API behind HTTPS and restrict `ALLOWED_ORIGINS` to the published operations and driver origins.
- Run a staging database and synthetic rescue scenario on every release.
- Back up PostgreSQL/WAL and rehearse restore; keep Kafka event retention long enough to rebuild realtime projections.
- Treat `/metrics` as internal telemetry and protect it at the ingress/network layer.
- Configure Sentry/OpenTelemetry and a managed OSRM instance before real traffic; the public OSRM endpoint is only a fallback for development.

## Store release checklist

Before publishing the driver app, configure the EAS project ID, signing credentials, Play Console service account, privacy policy URL, support email, screenshots, and a production `EXPO_PUBLIC_API_BASE_URL`. The repository includes `mobile/PRIVACY.md` as a starting point, not a substitute for the operator's legal notice.
