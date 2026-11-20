"""GCP billing kill-switch: Budget -> Pub/Sub -> this Cloud Function -> detach billing.

Deploy ONLY into your isolated/throwaway project (see ../README.md). Detaching the
billing account stops all paid services in that project (Vertex AI calls start failing).
Untested against a live project in Phase 1 - dry-run with DRY_RUN=1 first.
"""
import base64
import json
import os

import functions_framework
from googleapiclient import discovery

PROJECT_ID = os.environ["GCP_PROJECT_ID"]
DRY_RUN = os.environ.get("DRY_RUN", "0") == "1"
# Disable when actual cost reaches this fraction of the budget (budget notifications carry costAmount/budgetAmount).
THRESHOLD = float(os.environ.get("KILL_AT_FRACTION", "1.0"))


@functions_framework.cloud_event
def stop_billing(cloud_event):
    data = json.loads(base64.b64decode(cloud_event.data["message"]["data"]).decode())
    cost, budget = float(data.get("costAmount", 0)), float(data.get("budgetAmount", 0))
    print(f"budget notification: cost={cost} budget={budget}")
    if budget <= 0 or cost < budget * THRESHOLD:
        print("under threshold, no action")
        return
    svc = discovery.build("cloudbilling", "v1", cache_discovery=False)
    name = f"projects/{PROJECT_ID}"
    info = svc.projects().getBillingInfo(name=name).execute()
    if not info.get("billingEnabled"):
        print("billing already disabled")
        return
    if DRY_RUN:
        print(f"DRY_RUN: would disable billing on {name}")
        return
    svc.projects().updateBillingInfo(name=name, body={"billingAccountName": ""}).execute()
    print(f"BILLING DISABLED on {name}")
