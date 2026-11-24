---
title: Science Lab
section: Build
order: 33
summary: Agent Studio's own research workspace for biology, chemistry, physics and maths, with open data sources and evidence behind every claim.
tags: science, research, biology, chemistry, physics, math, pubmed, uniprot, pubchem, arxiv, evidence, claims, hypotheses
routes: /science/
---
The **Science Lab** is a research workspace for biology, chemistry, physics and mathematics. It reads open databases, calculates exactly on your PC, and keeps the evidence behind every claim.

![The Science Lab](shot:science-home)

> It is our own lab. It does not connect to Claude Science or Google's co-scientist.

## What it reads

Thirteen open, **read-only** sources: PubMed, Europe PMC, UniProt, AlphaFold, RCSB PDB, PubChem, arXiv, INSPIRE-HEP, NASA ADS (needs a token), OEIS, Wolfram|Alpha (needs your AppID), OpenAlex (needs a free key) and Crossref. One careful client talks to all of them: a limit per host, a cache, respect for rate limits, a size cap, and a record of where every answer came from. A missing key, a rate limit or an unreachable source is reported as such. An empty result says "no data found".

## What it calculates

On this PC, with no network and no `eval`: a safe expression calculator, units with dimension checking, CODATA 2022 constants with source and uncertainty, molar mass from a formula, DNA and RNA sequence statistics, and numeric calculus. Your own Python runs only in the sandbox and leaves a reproducibility record (code hash, inputs, seed, output hash).

## Investigations

An **investigation** holds a question, competing hypotheses ranked by pairwise judgements (Elo), evidence, claims, and a record of every call.

- Evidence must come from a recorded call; a reference the call did not return is refused.
- A claim's label is **worked out by the server from its evidence**, never chosen by a model or a person: *established* (two or more separate works or records support it; the same paper in two databases counts once), *single source*, *computed here*, *model inference*, *conflicting* or *unsupported*.
- DOIs and PMIDs are checked against Crossref and PubMed.
- The **report** is Markdown with each claim's label, an evidence table with retrieval times, computations and limits, plus `evidence.json` and `run.json`, kept as [artifacts](doc:artifacts).

> "Established" means two separate records support a claim. It is not a judgement that the claim is true.

## Tabs

**Investigation**, **Search**, **Compute**, **Report**, **Safety**, **Components** and **Settings**. Charts and tables reuse the [rich components](doc:rich-answers).

## Agents

A built-in **Science Lab** agent, eleven `science_*` tools and seven skills (literature review, hypothesis ranking, chemistry, biology, physics, maths, safety). The tools exist only while the lab is on.

## Optional components

PaperQA2 and ToolUniverse can be installed, each into its own environment, after you read the plan (licence, size, keys, where data goes) and tick the licence box. Some tools, such as Biomni, ChemCrow, AI Scientist v2 and Edison, are listed as not installable here, with the reason.

## Safety

A screen runs on every request. It **blocks** asking for ways to make, scale up, obtain, weaponise or strengthen chemical-weapon agents, toxins, dangerous pathogens or explosives, and **asks you to confirm** other risky wet-lab procedures. Education, medicine, history and detection are allowed. The level can be raised but never lowered, and there is no switch that turns it off. Blocks are recorded by text length only.

> The screen is a set of word patterns, so it can be bypassed. It reduces risk and is not a guarantee. Hazardous work needs institutional review.

## Limits

There are no retrosynthesis, reaction prediction or sequence design tools, and no 3D molecule viewers yet. The suggest, approve, autonomous mode is stored but does not gate steps yet. Full detail is in `docs/SCIENCE-LAB.md`.
