---
title: Run a security check
section: How-to
order: 77
summary: Read the scorecard, fix the weakest agent, attack it on purpose, and see the score change.
tags: how to, security, scorecard, red team, fix, harden, evaluation
---
## Steps

1. Open **Security**. Read the overall scorecard.
2. Switch to **Per agent**. The weakest agent is at the top, with its top issue.
3. Open its card and press a fix link. It opens the agent editor at the right section. Make the change and save.
4. Back on **Security**, check that the score went up. Where a check cannot apply to this agent, the card says why.
5. Set up a [guardrail profile](doc:guardrails) if you have none.
6. Run a [red team](doc:red-team) against the agent. Use **promptfoo in Local only mode** if you do not want anything sent out.
7. Make or run an [evaluation](doc:evaluations) with the questions that matter to you.
8. Check that the scorecard now shows both as tested. They only count if the run finished and did real work.

> The scorecard measures your configuration, not whether an attack works. Red team and evaluations are how you find that out.

See [Security](doc:security).
