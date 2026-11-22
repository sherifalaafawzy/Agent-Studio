---
name: pipeline-review
group: Sales
mcp:
- hubspot
- excel
description: 'Review a sales pipeline export: stage conversion, stalled deals, forecast risk and the five deals to focus on this week. Use for weekly pipeline reviews and forecast calls.'
---

# Pipeline review

**When to use:** a CRM export (deals with stage, amount, owner, created/last-activity/close dates) needs a review.

1. Check the data first: missing close dates or amounts, close dates in the past, duplicates. Report the count; exclude bad rows from totals and say so.
2. Compute: pipeline by stage (count, value), weighted value (use the user's stage probabilities, or say you used none), average age per stage, win rate if closed deals are present.
3. Find risk: no activity in 14+ days, stuck in a stage 2x longer than the stage average, close date pushed more than twice, single-threaded (one contact), large deals with no next step.
4. Give a forecast range (commit / best case) with the assumptions, never a single falsely-precise number.
5. End with the **five deals to act on this week** (why, next step, owner) and the three data-hygiene fixes with the biggest effect.

**Safety:** reports only. Do not edit deals or message customers.
