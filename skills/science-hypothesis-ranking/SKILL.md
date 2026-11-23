---
name: science-hypothesis-ranking
group: Science
description: "Generate competing hypotheses, critique them against the evidence and rank them pairwise (Elo). Use when a question has several plausible explanations."
---

# Science: hypothesis ranking

**When to use:** a mechanism, cause or design question with more than one plausible answer.

1. Write 3 to 6 distinct hypotheses (`science_add_hypothesis`), each testable and one sentence long.
2. For each, list what evidence would support it and what would refute it. Search for exactly that (`science_search`, `science_calc`).
3. Judge pairs: for each pair ask which is better supported by the recorded evidence, and record the result with `science_compare`. Do not let the order you wrote them decide.
4. Attach the evidence for the top hypotheses and record claims. Say clearly when the ranking rests on thin or conflicting evidence.
5. Report the ranking with its caveats: ranks are judgements, not measurements.

**Safety:** hypotheses about making, enhancing or obtaining hazardous agents are not helped with.
