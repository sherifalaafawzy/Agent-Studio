---
name: model-evaluation-report
group: Machine Learning
mcp:
- mlflow
- trainer
description: 'Evaluate a trained model beyond one metric: slices, calibration, errors, robustness and a deployment recommendation. Use before a model is shipped or compared with another.'
---

# Model evaluation report

**When to use:** a model and a held-out set exist, and someone must decide whether it is good enough.

1. Confirm the evaluation set was never used for training or tuning. If unsure, say so prominently.
2. Headline metrics with **confidence intervals** (bootstrap) and the baseline next to them.
3. **Slices:** metrics by important segments (region, product, time period, class). Large gaps are findings even when the average looks fine.
4. **Calibration:** do predicted probabilities match frequencies? Threshold choice versus the cost of each error type.
5. **Errors:** look at the worst 20 mistakes and group them; note label noise.
6. **Robustness:** performance on the most recent period, on shifted inputs, and sensitivity to missing features.
7. Recommendation: ship / ship with monitoring / do not ship, with the monitoring metrics and the drift triggers that would reopen the decision. Log the run to MLflow only when the user has connected it.

**Safety:** report; do not promote or deploy a model automatically.
