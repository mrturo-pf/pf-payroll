# Deployment Guide

CI/CD pipeline, Google Cloud Run deployment, and production configuration for pf-payroll.

## Overview

The service is deployed to **Google Cloud Run** via **GitHub Actions** (`.github/workflows/deploy.yml`).

**Key characteristics:**
- Automatic deployment on push to `main` (after manual approval)
- Trivy security scanning (blocks on CRITICAL/HIGH vulnerabilities)
- Multi-stage Docker build with non-root final image
- Scale-to-zero configuration (min 0 instances)
- Shared database managed by [pf-db](../pf-db)

## Pipeline jobs

`.github/workflows/deploy.yml` runs **six jobs**:

| Job | Trigger | Action |
|---|---|---|
| `test` | PR + push `main` | lint, vulture, mypy, jscpd, pytest+coverage |
| `build` | PR + push `main` | Docker build, Trivy scan (SARIF + blocking gate on CRITICAL/HIGH) |
| `gate` | push `main` | manual approval via `production` environment |
| `deploy` | push `main` | push image to AR, deploy Cloud Run |
| `notify-failure` | any job failure on `main` | SMTP failure email |
| `notify-success` | successful deploy | SMTP success email |

### Workflow

```
PR opened/updated
  └─> test job
        └─> build job (scan only, no push)

Push to main
  └─> test job
        └─> build job (scan + upload artifact)
              └─> gate job (manual approval)
                    └─> deploy job (push to AR, deploy to Cloud Run)
                          ├─> notify-failure (on error)
                          └─> notify-success (on success)
```

## Pipeline invariants

**Never violate these rules:**

1. **Migrations before traffic** — the `pf-db` Cloud Run Job must apply all pending migrations before either service receives traffic. pf-payroll ships no migration tooling.

2. **DB URL via `--set-secrets` only** — never `--set-env-vars`
   ```bash
   #  Correct
   --set-secrets=PF_DATABASE_URL=pf-db-url:latest
   
   #  Wrong (exposes secret in gcloud describe)
   --set-env-vars=PF_DATABASE_URL=postgresql://...
   ```

3. **AR scanning stays disabled** — pipeline uses Trivy (~$5/month if enabled)
   ```bash
   # Artifact Registry scanning is intentionally disabled
   # Trivy runs in the pipeline instead (free, faster)
   ```

4. **`--min-instances=0`** — intentional scale-to-zero; do not change without approval
   - Zero compute cost when idle
   - Cold starts acceptable for this use case

5. **Image tagged with both `github.sha` and `latest`** — deploy references SHA, not `latest`
   ```bash
   # Both tags are pushed
   us-central1-docker.pkg.dev/PROJECT/pf-payroll/app:abc123def
   us-central1-docker.pkg.dev/PROJECT/pf-payroll/app:latest
   
   # Deploy uses SHA for immutability
   --image=us-central1-docker.pkg.dev/PROJECT/pf-payroll/app:abc123def
   ```

6. **Non-root container** — Dockerfile switches to `appuser` in final stage
   ```dockerfile
   # Final stage runs as non-root
   USER appuser
   CMD ["uvicorn", "payroll.interfaces.api.main:app", ...]
   ```

7. **Multi-stage build** — final stage copies only the venv; do not add `COPY alembic.ini`, `COPY alembic`, or `COPY src ./src`
   ```dockerfile
   #  Correct (only venv)
   COPY --from=builder /app/.venv /app/.venv
   
   #  Wrong (duplicates source, breaks PATH)
   COPY src ./src
   ```

## GitHub Secrets

Configure the following secrets in the repository (Settings → Secrets and variables → Actions):

| Secret | Required | Description |
|---|:---:|---|
| `GCP_SA_KEY` |  | Service-account JSON key with roles: `roles/run.admin`, `roles/iam.serviceAccountUser`, `roles/artifactregistry.writer` |
| `GCP_PROJECT_ID` |  | GCP project ID (e.g., `my-project-123`) |
| `PF_DATABASE_URL` |  | Connection string stored in Secret Manager, injected into Cloud Run at runtime |
| `GCP_CLOUD_SQL_INSTANCE` | optional | Cloud SQL instance in `PROJECT:REGION:INSTANCE` format (leave empty for external DB) |
| `MAIL_SERVER` |  | SMTP server hostname (e.g., `smtp.gmail.com`) |
| `MAIL_PORT` |  | SMTP port (e.g., `587` for STARTTLS) |
| `MAIL_USERNAME` |  | SMTP username / sender address |
| `MAIL_PASSWORD` |  | SMTP password or app-specific password |
| `MAIL_FROM` |  | Sender display address (e.g., `pf-payroll CI <you@gmail.com>`) |
| `MAIL_TO` |  | Recipient address(es), comma-separated |

### Database options

The pipeline supports two database configurations:

**Option A — External DB** (Neon, Supabase, etc.):
- Set `PF_DATABASE_URL` in Secret Manager pointing to the external host
- Leave `GCP_CLOUD_SQL_INSTANCE` **empty**

**Option B — Cloud SQL**:
- Set `PF_DATABASE_URL` in Secret Manager with `localhost` (proxy sidecar handles connection)
- Set `GCP_CLOUD_SQL_INSTANCE=PROJECT:us-central1:pf-db`
- Pipeline adds Cloud SQL proxy sidecar automatically

## Cloud Run configuration

| Setting | Value | Notes |
|---|---|---|
| **Region** | `us-central1` | Must match Artifact Registry region |
| **Min instances** | `0` | Scale to zero when idle |
| **Max instances** | `2` | Prevent runaway scaling |
| **Memory** | `512 MiB` | Sufficient for payroll workloads |
| **CPU** | `1` | Single vCPU |
| **Port** | `8000` | FastAPI default (Cloud Run injects `PORT` env var) |
| **Service account** | `pf-payroll@<PROJECT>.iam.gserviceaccount.com` | Needs `roles/secretmanager.secretAccessor` |
| **Secrets** | `PF_DATABASE_URL` from Secret Manager | Never use `--set-env-vars` |

### Service account permissions

The Cloud Run service account needs:

```bash
# Allow reading secrets at runtime
gcloud projects add-iam-policy-binding PROJECT_ID \
  --member="serviceAccount:pf-payroll@PROJECT_ID.iam.gserviceaccount.com" \
  --role="roles/secretmanager.secretAccessor"
```

## One-time GCP setup

See the comment block at the top of `.github/workflows/deploy.yml` for the full bootstrap sequence:

1. Enable APIs (`run.googleapis.com`, `artifactregistry.googleapis.com`, `secretmanager.googleapis.com`)
2. Create Artifact Registry repository (`us-central1`, format `docker`, name `pf-payroll`)
3. Create Secret Manager secret (`pf-db-url`) with connection string
4. Create service account for Cloud Run with `roles/secretmanager.secretAccessor`
5. Grant GitHub Actions service account necessary roles
6. Configure GitHub Secrets

## Manual deployment

If you need to deploy manually (e.g., for testing):

```bash
# 1. Authenticate
gcloud auth login
gcloud config set project PROJECT_ID

# 2. Build and push
docker build -t us-central1-docker.pkg.dev/PROJECT_ID/pf-payroll/app:manual .
docker push us-central1-docker.pkg.dev/PROJECT_ID/pf-payroll/app:manual

# 3. Deploy
gcloud run deploy pf-payroll \
  --image=us-central1-docker.pkg.dev/PROJECT_ID/pf-payroll/app:manual \
  --region=us-central1 \
  --platform=managed \
  --set-secrets=PF_DATABASE_URL=pf-db-url:latest \
  --min-instances=0 \
  --max-instances=2 \
  --memory=512Mi \
  --cpu=1 \
  --port=8000 \
  --service-account=pf-payroll@PROJECT_ID.iam.gserviceaccount.com \
  --allow-unauthenticated
```

## Rollback

Cloud Run keeps previous revisions. To rollback:

```bash
# List revisions
gcloud run revisions list --service=pf-payroll --region=us-central1

# Rollback to a specific revision
gcloud run services update-traffic pf-payroll \
  --region=us-central1 \
  --to-revisions=pf-payroll-00042-abc=100
```

## Monitoring

### Logs

```bash
# Stream logs
gcloud run services logs tail pf-payroll --region=us-central1

# View in Cloud Console
# https://console.cloud.google.com/run/detail/us-central1/pf-payroll/logs
```

### Metrics

```bash
# Request count, latency, error rate
gcloud monitoring dashboards list
```

### Alerts

Configure alerts in Cloud Monitoring for:
- Error rate > 5%
- p99 latency > 2s
- Instance count (should scale to 0 when idle)

## Troubleshooting

### Deployment fails: "Image not found"

**Cause:** Image push to Artifact Registry failed or used wrong tag.

**Solution:**
```bash
# Verify image exists
gcloud artifacts docker images list us-central1-docker.pkg.dev/PROJECT_ID/pf-payroll
```

### Deployment fails: "Service account does not have permission"

**Cause:** Cloud Run service account lacks `roles/secretmanager.secretAccessor`.

**Solution:**
```bash
gcloud projects add-iam-policy-binding PROJECT_ID \
  --member="serviceAccount:pf-payroll@PROJECT_ID.iam.gserviceaccount.com" \
  --role="roles/secretmanager.secretAccessor"
```

### Service returns 500: "Database connection failed"

**Cause:** `PF_DATABASE_URL` secret is incorrect or database is unreachable.

**Solution:**
1. Verify secret value in Secret Manager
2. If using Cloud SQL, ensure proxy sidecar is configured
3. Check pf-db is accepting connections

### Trivy scan blocks deployment

**Cause:** Critical or high-severity vulnerabilities detected.

**Solution:**
1. Update base image in Dockerfile (`python:3.12-slim` → newer version)
2. Update dependencies in `pyproject.toml`
3. Run `make reinstall` locally and retest
4. If vulnerability is in transitive dependency, check for newer versions

## Versioning

Deployments are tagged with **SemVer** based on commit history:

- **Major** (1.0.0 → 2.0.0): Breaking API changes
- **Minor** (1.0.0 → 1.1.0): New features, backward-compatible
- **Patch** (1.0.0 → 1.0.1): Bug fixes

Use **Conventional Commits** to trigger semantic versioning:
- `feat:` → minor bump
- `fix:` → patch bump
- `feat!:` or `BREAKING CHANGE:` → major bump

See [AGENTS.md](../AGENTS.md#versioning-and-operations) for commit conventions.
