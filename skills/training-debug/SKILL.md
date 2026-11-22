---
name: training-debug
group: Deep Learning
mcp:
- trainer
description: 'Debug a neural-network training run that does not learn or diverges: check data, loss, gradients and learning rate in a fixed order. Use when loss is flat, NaN or the model overfits instantly.'
---

# Debugging neural-network training

**When to use:** loss is NaN, flat, oscillating, or validation never improves.

Go in this order - most bugs are in the first three:

1. **Data:** look at real batches (shapes, ranges, labels aligned with inputs, no leakage, shuffling on for training). Normalisation applied to train *and* val with the same statistics.
2. **Overfit one small batch** (8-32 samples). If it cannot reach near-zero loss, the bug is in the model/loss/optimiser wiring, not the data volume.
3. **Loss and outputs:** right loss for the task (logits vs probabilities, class-index vs one-hot), correct reduction, label dtype. Initial loss should be close to ln(num_classes).
4. **Optimisation:** learning rate sweep (10x steps), warm-up, gradient clipping, optimizer state, mixed-precision overflow, `model.train()` vs `eval()` (dropout/batch-norm).
5. **Gradients:** check norms per layer for zeros/NaN/explosion.
6. Only then: capacity, regularisation, augmentation, longer training.

Change **one thing at a time** and log each run (seed, config, result). Use the trainer tool's `job` logs for the evidence; say what you could not verify.

**Safety:** use short runs first; ask before launching long or GPU-heavy jobs.
