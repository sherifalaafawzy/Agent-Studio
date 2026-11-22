---
name: hyperparameter-sweep-plan
group: Deep Learning
mcp:
- trainer
- mlflow
description: 'Plan an efficient hyper-parameter search with a budget: what to vary, ranges, method and stopping rules. Use before spending GPU time on tuning.'
---

# Hyper-parameter sweep plan

**When to use:** a working model exists and the user wants to tune it within a time or money budget.

1. Fix the budget (GPU-hours or cost) and the metric on a **validation** set; the test set stays untouched.
2. Rank parameters by usual impact: learning rate (log scale), batch size, weight decay, schedule/warm-up, dropout, width/depth. Tune the top two or three first.
3. Choose the method: a small random search beats a big grid; use successive halving / early stopping to kill bad runs early. Use multiple seeds for the finalists (differences smaller than seed noise are not real).
4. Write the plan as a table: parameter, range, scale (log/linear), fixed values, number of trials, epochs per trial, stop rule.
5. Estimate cost = trials x epochs x time per epoch, and show it **before** running. If it exceeds the budget, shrink the search rather than skipping validation.
6. Track every trial (config, metric, seed, duration); summarise with the best config and how much it improved over the default.

**Safety:** estimate first, ask for approval, then run. Never start cloud GPU resources without an explicit confirmation from the user.
