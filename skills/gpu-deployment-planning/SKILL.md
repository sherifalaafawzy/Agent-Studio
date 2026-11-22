---
name: gpu-deployment-planning
group: Deep Learning
mcp:
- cloudml
- aws-pricing
description: Choose where and how to train or serve a deep-learning model (local, SageMaker, Vertex AI, Azure ML, RunPod, ...) by comparing cost, latency and effort. Use before provisioning GPUs or endpoints.
---

# GPU and deployment planning

**When to use:** a model is ready to train at scale or serve, and a platform and instance type must be chosen.

1. Collect requirements: model size, precision, batch/latency target, traffic pattern (steady, bursty, scale-to-zero), data residency, team skills.
2. Memory first: rough GPU memory = parameters x bytes per parameter (+ activations / optimizer states when training; KV cache when serving). Pick the smallest GPU that fits with headroom.
3. Compare 2-3 options in a table: platform, instance/GPU, price per hour (look it up - say the date and source), cold-start, scaling, ops effort, lock-in. Use the **cloudml** tool's read-only `list` and `describe` to see what already exists in the account.
4. Compute a monthly estimate for serving (hours x price x replicas) and a per-run estimate for training. State the assumptions.
5. Recommend one option plus a cheap way to validate it (a short load test) and the rollback plan.

**Safety:** the cloudml tool is read-only by default. Any create/deploy/delete needs an explicit user confirmation that names the provider, resource and expected cost. Never run billable operations as part of planning.
