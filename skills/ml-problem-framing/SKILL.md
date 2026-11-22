---
name: ml-problem-framing
group: Machine Learning
mcp:
- huggingface
- arxiv
description: 'Turn a business problem into a well-posed machine-learning task: target, features, metric, baseline, data needs and risks. Use before any modelling starts.'
---

# ML problem framing

**When to use:** someone says "can we predict X?" or wants an ML solution.

1. Ask what decision the prediction changes and who acts on it. If no decision changes, say ML may not be needed.
2. Define the **task** (classification, regression, ranking, forecasting, clustering, anomaly detection), the **target** and exactly when it is known (label delay), and the **prediction time** (what information is available then).
3. Choose the **metric** to match the cost of errors (precision/recall, PR-AUC, MAE/MAPE, calibration) and a **baseline** (rule, last value, majority class). A model must beat the baseline to be worth it.
4. Check data: volume, label quality, class balance, history length, leakage risks (features that only exist after the outcome), drift risk, privacy and fairness concerns.
5. Output a one-page plan: task, target, metric, baseline, data needed, validation scheme (time-based split for temporal data), risks and a go/no-go criterion.

**Safety:** no training here, just framing. Flag sensitive attributes and regulatory limits instead of assuming they are fine.
