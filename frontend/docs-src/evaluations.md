---
title: Evaluations
section: Observe and protect
order: 54
summary: Test sets of prompts with checks, run against an agent with results, cost and history, and export for other tools.
tags: evals, evaluation, test sets, checks, judge, deepeval, ragas, promptfoo, inspect, regression
routes: /evals/
---
**Evaluations** are test sets: prompts with checks, run against one of your agents so you can see whether a change made it better or worse.

![Evaluations](shot:evals)

## Build a test set

1. Open **Evaluations** and create a set.
2. Add cases: a prompt and one or more checks.
3. Choose the checks: **contains**, **equals**, **regex**, **JSON valid**, **length**, or a **judge model** that grades the answer.

## Run it

Pick an agent and run. You get a pass or fail for every case, the answer, the cost, and a history of earlier runs to compare.

## Export

- A **set** exports for promptfoo or Inspect.
- A finished **run** exports for DeepEval, Ragas, CSV or JSONL.

## The scorecard

A finished run with at least one case answered counts as evidence that agents are tested regularly. See [Security](doc:security#what-counts-as-evidence).
