# Science Lab

Agent Studio's own research workspace for **biology, chemistry, physics and mathematics**. It reads open scientific databases, computes exactly on your PC, keeps hypotheses and the evidence behind every claim, and writes a report in which each claim shows how well it is supported. Open it from **Science** in the sidebar (`/science`).

**Excluded by decision:** Claude Science and Google's co-scientist / Gemini for Science. Agent Studio does not connect to, drive or install them (neither has a public interface we could rely on, and you asked for our own lab). They are not in the component catalogue. The design research is kept in the project notes; it is not code.

## What it does

| Area | What you get |
|---|---|
| **Open data sources** (read-only) | PubMed, Europe PMC, UniProt, AlphaFold DB, RCSB PDB, PubChem, arXiv, INSPIRE-HEP, NASA ADS (token), OEIS, Wolfram\|Alpha (your AppID, never cached), OpenAlex (free key), Crossref. One careful HTTP client: a limit per host, short cache, `Retry-After` honoured, 2 MB cap, identifying User-Agent. Every answer carries provenance: source, query, when it was fetched, licence, whether it came from the cache. "No data found" is a result; rate limits, a missing key and an unreachable source are reported as such. |
| **Local computation** (no network, no `eval`) | A safe expression calculator, units with dimension checking, CODATA 2022 constants (with source and uncertainty), molar mass from a formula, DNA/RNA sequence statistics (counting and translating only), numeric integral / derivative / root / table. Everything is labelled "computed here". |
| **Your own code** | `science_run_python` runs only in the sandbox (no network by default), is safety-screened first and leaves a reproducibility record: code hash, inputs, seed, output hash, environment. |
| **Investigations** | A question, competing hypotheses ranked by pairwise judgements (Elo), evidence, claims, a record of every call and run. |
| **Honest claims** | A claim's label is *computed by the server from its evidence*, never chosen by a model or a person: **established** (two or more separate works or records support it; the same paper found in two databases counts once), **single source**, **computed here**, **model inference**, **conflicting**, **unsupported** (no usable evidence). Evidence must come from a recorded data-source call or computation: a reference the call did not return is refused. |
| **Citation checks** | DOIs and PMIDs are checked against Crossref and PubMed. Found = verified; not found = flagged "could not verify"; source unreachable = "not checked". Nothing is silently dropped. |
| **Report and notebook** | Markdown report (claims with labels, evidence table with retrieval times, computations, limits) plus `evidence.json` and `run.json`, kept as artifacts (new versions each time). |
| **Agents** | A built-in **Science Lab** agent, eleven `science_*` tools, and skills for literature review, hypothesis ranking, chemistry, biology, physics, maths and safety. The tools exist only while the lab is on. Recording tools are allowed in plan mode; running Python is not. |
| **Optional components** | PaperQA2 and ToolUniverse can be installed, each into its own virtual environment, after you read the plan (licence, size, hardware, keys, where data goes, cost) and tick the licence box. Pinned versions, ready-made wheels only, the 14-day rule. Biomni, ChemCrow and AI Scientist v2 are listed with the reason they cannot be installed yet. Edison / FutureHouse is listed as a hosted service (no install, no connector until its terms are confirmed). |

## Safety (dual-use)

A **risk reducer, not a guarantee**: word patterns can be bypassed. It is layered:

1. **Screen** on every request, search, hypothesis, investigation title/question and piece of code. *Block*: asking for a way to make, scale up, obtain, weaponise or enhance chemical-weapon agents, toxins, dangerous pathogens or explosives. *Confirm*: wet-lab procedures that name a toxin or pathogen. *Allow*: education, hazards, history, medicine, detection, decontamination, regulation. Blocks are explained in plain words, never silent.
2. **Tools that do not exist here**: retrosynthesis, reaction prediction, sequence or toxin design, anything that orders or makes a physical substance. Connectors only read public reference databases.
3. **Budgets and limits**: calls per investigation, per-host request spacing, size caps.
4. **Audit**: blocks and flags are written to the guardrail events table (the *length* of the text only, never the text) and sent to the security export as findings; the AI-BOM lists the sources that are on and where queries go (never questions, evidence or keys).
5. **Level**: `standard` or `strict` (strict also blocks wet-lab procedures with biological hazards). It can be **raised, never lowered, and there is no switch that turns it off**.
6. The same screen is available as a guardrail kind (**Science safety**) so any profile can apply it to ordinary chats.

Tests use abstract, category-labelled inputs built from the word lists. No hazardous prompt is stored in the repository. Hazardous work needs institutional review whatever this tool says.

## Where things are

* Backend: `backend/app/science/` (`safety`, `fetch`, `connectors`, `compute`, `service`, `catalog`, `tools`, `api`), `backend/app/guardrails/science.py`, tables `science_*` in `db.py`, settings `science_config` / `science_secrets` / `science_components`.
* Frontend: `frontend/app/science/page.tsx`, `frontend/components/science/*`, `frontend/lib/science.ts`.
* Skills: `skills/science-*`. Built-in agent: `science_lab`.
* API: `/api/science/*` (config, keys, connectors, screen, search, calc, constants, investigations, evidence, claims, hypotheses, compare, verify, report, notebook, components).

## What is not built (honest list)

* **Molecule / protein / equation viewers** (3Dmol.js, RDKit.js): not added. They would need new pinned npm packages and WASM; PubChem results show formula, mass, SMILES and links, proteins show accession, length and mean pLDDT, and the structure is opened at the source. Tables and charts reuse the AG-UI Kit (sorting, export, table view). KaTeX is already used by the chat renderer.
* **Named science sandbox images** (`sci-base`, `sci-chem`, `sci-bio`, `sci-lean`): not built. `science_run_python` uses the workspace sandbox as it is (numpy/sympy only if that image has them). No RDKit, Biopython or Lean here.
* **Autonomy modes** (suggest / approve / autonomous) are stored on an investigation but do not yet gate an agent's steps (gating needs per-step approval inside chat; out of scope for v0.1.0).
* **Built since (2026-11-27):** a **debate workflow template** (*Science debate*: proposer, sceptic, proposer answers, judge with claim labels; `app/workflows/builder.py`), two **ready-made evaluation sets** on the Evals page (*citation validity*: real DOIs/arXiv ids, an invented paper, claim labels; *refusal behaviour*: four requests the safety screen blocks plus three it must not over-refuse, built from the screen's own category words so no hazardous prompt is stored; `app/evals/starters.py`), a **Science Lab section on the security scorecard** (safety level, network-free compute, call budget, whether the two sets were run; shown separately, not in the overall score; `app/security/scorecard.py`), and the docs page `science-lab` with a screenshot. A *retrieval accuracy* set is not included: it needs a fixed reference corpus.
* **External agents**: Edison / FutureHouse and AlphaEvolve adapters are out of scope for v0.1.0 (terms and access unverified).
* **Named science sandbox images**: out of scope for v0.1.0 (see above).
* Sources whose limit or licence was not confirmed from the source are marked "not verified" in the UI and kept conservative (Europe PMC, UniProt, INSPIRE-HEP, OEIS, OpenAlex, Crossref).
* Not installable and why: Biomni (conda environment and an ~11 GB data download, runs model-written code), ChemCrow (contains reaction/synthesis tools; also needs Python below 3.12), AI Scientist v2 (licence not confirmed; GPU; runs model-written code).
