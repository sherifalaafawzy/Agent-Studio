---
name: science-literature-review
group: Science
description: "Review the literature on a scientific question with real, checked citations and an honest evidence table. Use for any 'what is known about X' question in biology, chemistry, physics or maths."
---

# Science: literature review

**When to use:** a question needs what the papers say, not what you remember.

1. `science_start` with the question, then `science_sources` to see which sources are on and which need a key.
2. Search at least two independent sources for the same question (for example PubMed and Europe PMC; arXiv and INSPIRE; OpenAlex and Crossref). Note the query you used.
3. Read the returned records only. Do not add details that are not in them. If a source returns nothing say "no data found" and, if useful, try one differently worded query.
4. For each finding: `science_add_evidence` (kind source, with the run_id and the record id), then `science_add_claim` listing the evidence ids. Two separate papers or records are labelled established (one paper found in two databases counts once); one is single source; your own reasoning is inference.
5. Run `science_check_citations`. Report any DOI or PMID that could not be verified; never drop them.
6. Finish with `science_report` and write the answer from it: claims with their labels, what conflicts, what is missing, how old the data is.

**Safety:** reading public databases only. Do not give medical, legal or safety advice; say so and point to guidelines. If a request is blocked by the safety screen, explain why and offer the safe alternative (education, hazards, history, regulation).
