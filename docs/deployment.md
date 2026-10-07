# Deployment

How the execution agent gets from a commit on `main` to a live,
callable Cloud Run service. See [architecture.md](architecture.md) for
how the app itself is built, and [api-usage.md](api-usage.md) for how
to call it once deployed.

## Infrastructure summary

| Thing | Value |
|---|---|
| GCP project | `compass-execution-agent` |
| Billing account | Linked, active (see "Billing" below for history) |
| Cloud Run service | `compass-execution-agent`, region `us-central1` |
| Live URL | Not published here, see [api-usage.md](api-usage.md) for how to look it up |
| Secrets | Google Secret Manager, 7 secrets (see below) |
| CI | GitHub Actions (`.github/workflows/ci.yml`), required check on `main` |
| CD | Cloud Build trigger, auto-deploys on push to `main` |

## How a deploy actually happens

1. A PR merges into `main` (blocked unless the `ci` GitHub Actions check
   passes, branch protection is configured on `main`).
2. A Cloud Build trigger (connected via "Connect to repo" in the Cloud
   Run console) fires on the push, builds the `Dockerfile` at the repo
   root, and deploys the resulting image as a new Cloud Run revision.
3. The new revision gets 100% of traffic once it's healthy.

There is currently **no automated post-deploy verification**: nothing
confirms the new revision actually works beyond Cloud Run's own health
checks. Verifying a deploy manually:

```bash
SERVICE_URL=$(gcloud run services describe compass-execution-agent \
  --project=compass-execution-agent --region=us-central1 \
  --format="value(status.url)")
curl "$SERVICE_URL/health"

gcloud secrets versions access latest --secret=COMPASS_API_KEY --project=compass-execution-agent
# then use that key for a real /chat call, see api-usage.md
```

(Tracked as SCRUM-25 to automate this.)

## Secrets

All in Secret Manager, wired into the Cloud Run service as environment
variables (`--set-secrets` at deploy time, persisted on the service
config so it carries across auto-deploys):

- `ALPACA_PAPER_KEY_ID` / `ALPACA_PAPER_SECRET_KEY`
- `GROQ_API_KEY`
- `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` / `LANGFUSE_BASE_URL`
- `COMPASS_API_KEY`

None of these are baked into the Docker image or committed to the
repo. `.env.example` documents the names; real values live only in
Secret Manager and in each developer's local `.env` (gitignored).

To update a secret:

```bash
printf '%s' "new-value" | gcloud secrets versions add SECRET_NAME \
  --project=compass-execution-agent --data-file=-
```

New versions take effect on the *next* deploy (or a manual
`gcloud run services update` to pick up `:latest`),  existing running
revisions keep whatever version they were deployed with.

## IAM

**Build-time**: Cloud Build's default compute service account needs
read access to the source upload bucket.

```bash
gcloud projects add-iam-policy-binding compass-execution-agent \
  --member="serviceAccount:PROJECT_NUMBER-compute@developer.gserviceaccount.com" \
  --role="roles/storage.objectViewer"
```

**Runtime**: the same service account needs to actually read the
secrets at container startup.

```bash
gcloud projects add-iam-policy-binding compass-execution-agent \
  --member="serviceAccount:PROJECT_NUMBER-compute@developer.gserviceaccount.com" \
  --role="roles/secretmanager.secretAccessor"
```

Without the second one, the build succeeds but the revision fails to
start with a clear `PERMISSION_DENIED` naming each secret.

## CI (`.github/workflows/ci.yml`)

Runs on every push/PR to `main`: `ruff check` (scoped rule set, `E`,
`F`, `I` only, deliberately not the full default, since some of ruff's
opinionated rules like `BLE001` flag patterns used intentionally in
this codebase) and an import-cleanliness check using placeholder env
vars (never real secrets, never calls a real API).

Branch protection on `main` requires this check to pass before a PR
can merge. This is how deploys are gated,  Cloud Build itself has no
native "wait for a GitHub status check" mechanism, so gating happens at
the merge step instead, which has the same practical effect (nothing
broken ever reaches `main`, so nothing broken ever gets built/deployed).

CI does **not** currently run the (nonexistent) test suite,  there
isn't one yet (SCRUM-20). Once `AlpacaBroker` has pytest coverage, it
gets wired into this same workflow.

## Billing


- A billing account must be linked to the project before Cloud Run,
  Secret Manager, or Artifact Registry APIs can be enabled.
- Adding a payment method can hit opaque verification failures (saw
  `OR_BAOOC_15`, undocumented by Google) that require waiting out a
  cooldown period or escalating to Google's billing support directly.
- Google places a small temporary authorization hold on the card during
  verification (saw ~MYR 120), released within about a week.
- A budget alert is configured (5 MYR threshold, 50%/100% notifications)
  as a safety net given real usage has been trivial against the Cloud
  Run free tier (2M requests/month, scales to zero when idle), not a
  hard spending cap, just an early warning.

## Reproducing this setup from scratch

Roughly, in order:

1. `gcloud projects create <project-id>`
2. Link an active billing account (`gcloud billing projects link`)
3. `gcloud services enable run.googleapis.com secretmanager.googleapis.com artifactregistry.googleapis.com`
4. Grant the two IAM roles above
5. Create the 7 secrets (`gcloud secrets create NAME --data-file=-`)
6. `gcloud run deploy --source=. --set-secrets=...` for the first manual
   deploy
7. In the Cloud Run console, "Connect to repo" on the service, authorize
   the GitHub App, set the branch trigger to `^main$`, Dockerfile build
   type
8. Set up branch protection on `main` requiring the `ci` check
   (`gh api repos/OWNER/REPO/branches/main/protection --method PUT`)
