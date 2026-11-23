---
name: science-math
group: Science
description: "Mathematics: numeric checks, calculus, integer sequences (OEIS) and literature. Use for calculations, sanity checks and finding known results."
---

# Science: math

**When to use:** a computation, a numeric check of a claimed identity, an integer sequence or a known theorem.

1. Compute with `science_calc` kind calc and show the expression. Results are floating point except integers: do not call them proofs.
2. Check an identity numerically at several points (table); a numeric check supports but does not prove it. Say that.
3. Integer sequences: oeis search or lookup; cite the A-number and credit the OEIS (CC BY-SA 4.0).
4. Heavier symbolic work: `science_run_python` in the sandbox if sympy exists there; otherwise say it cannot be done here.
5. Wolfram|Alpha is optional and needs your key; its answers must not be stored beyond its terms.

**Safety:** none beyond the general rules.
