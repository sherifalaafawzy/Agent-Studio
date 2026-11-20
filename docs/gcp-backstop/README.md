# GCP backstop (defence in depth behind the in-app guard)

> **Status: documented scaffold. Not yet exercised against a live GCP project.** Run it in an
> **isolated / throwaway project** that contains nothing else — detaching billing stops *every* paid service in it.

The in-app per-provider guard (`backend/app/budget.py`) blocks spend *before* each call, but it can only see
traffic that flows through Agent Studio, and it works from token **estimates**. This backstop catches what it cannot:
other clients using the same project/credentials, a bug, or estimate-vs-billing drift.

| Layer | Stops | Latency | Catches |
|---|---|---|---|
| In-app guard (cap per provider) | the call, before it is sent | instant | everything through the app |
| **Vertex quotas** (lowered) | requests at the API | instant | everything on the project, incl. other clients |
| **Budget → Pub/Sub → Function** | all paid services (detach billing) | **hours** (billing data lags) | runaway spend that slipped past the others |

**Residual risk (be honest with yourself):** Cloud Billing data is delayed by hours, so the kill-switch limits the
*overshoot*, it does not prevent it. Quotas are the only instant project-wide limit. Keep the in-app caps and the budget
amount **well below** what you can afford to lose.

## 1. Lower the Vertex quotas (instant, project-wide)
Console → IAM & Admin → Quotas → filter `aiplatform.googleapis.com` → lower
*Generate content requests per minute per base model* (and tokens-per-minute where offered) for the models you use.
A low RPM/TPM ceiling bounds worst-case burn rate even if every other layer fails.

## 2. Budget + Pub/Sub topic
```powershell
$P = "your-throwaway-project"; $BA = "XXXXXX-XXXXXX-XXXXXX"   # billing account id
gcloud config set project $P
gcloud services enable cloudbilling.googleapis.com pubsub.googleapis.com cloudfunctions.googleapis.com run.googleapis.com cloudbuild.googleapis.com budgets.googleapis.com
gcloud pubsub topics create budget-alerts
gcloud billing budgets create --billing-account=$BA --display-name="agent-studio-kill" `
  --budget-amount=10USD --filter-projects="projects/$P" `
  --threshold-rule=percent=0.5 --threshold-rule=percent=0.9 --threshold-rule=percent=1.0 `
  --notifications-rule-pubsub-topic="projects/$P/topics/budget-alerts"
```

## 3. Deploy the kill-switch function (start with DRY_RUN=1)
```powershell
gcloud functions deploy stop-billing --gen2 --runtime=python312 --region=us-central1 `
  --source=docs/gcp-backstop/cloud-function --entry-point=stop_billing `
  --trigger-topic=budget-alerts --set-env-vars="GCP_PROJECT_ID=$P,DRY_RUN=1,KILL_AT_FRACTION=1.0"
```
Grant the function's service account permission to detach billing — **Project Billing Manager** on the project
(`roles/billing.projectManager`) is the narrowest role that can call `updateBillingInfo`; granting it on the *billing
account* is also required to unlink. Review IAM carefully; this is a powerful permission.

Test: publish a synthetic message (`costAmount` ≥ `budgetAmount`) to `budget-alerts` and read the function logs for
`DRY_RUN: would disable billing`. Only then redeploy with `DRY_RUN=0`.

To recover after a trip: Console → Billing → re-link the project to the billing account.

## 4. Service account for Agent Studio
Create a dedicated service account with **only** `roles/aiplatform.user`, download its JSON key to a path **outside the
repo**, and put that path in Settings → Vertex AI → *Service-account JSON path*. (`sa-key*.json` is git-ignored as a
safety net.)
