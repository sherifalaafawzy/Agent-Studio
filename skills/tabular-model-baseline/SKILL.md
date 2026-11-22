---
name: tabular-model-baseline
group: Machine Learning
mcp:
- trainer
- duckdb
description: Build and evaluate a quick tabular baseline (Ludwig/PyTorch/Keras via the trainer tool) on a CSV, with a proper split and an honest report. Use for the first model on structured data.
---

# Tabular model baseline

**When to use:** a CSV with a target column, and the goal is a first honest model.

1. Check the framing (see ml-problem-framing) and run the data-quality checks. Decide the split **before** looking at results: random for iid data, time-based for temporal data, grouped when rows of one entity must not straddle the split.
2. Compute the dumb baseline (majority class / mean / last value) on the test split.
3. Use the **trainer** tools: `status` (is the framework installed? if not, tell the user to install it from Catalog > ML tools - sizes and licences are shown there), `templates`, then `train` with a small epoch count first. Poll `job` until it finishes; use `cancel` if it goes wrong.
4. `evaluate` on the held-out file only once. Report the metric next to the baseline, plus a confusion matrix or residual summary, and the training log's warnings.
5. Be honest: a model close to the baseline is a result. Do not tune on the test set. Mention leakage checks and the seed.

**Safety:** training is local and uses CPU/GPU time; ask before long jobs. Do not upload data to cloud services unless the user chose that.
