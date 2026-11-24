---
title: Billing and spend limits
section: Work
order: 16
summary: How spend limits are enforced before each request, and how to read the dashboard and billing screens.
tags: billing, spend, cap, limit, cost, budget, dashboard, credits, tokens
routes: /billing/, /dashboard/
---
Online models cost money. Agent Studio protects you with a **hard spend limit per provider** that is checked *before* every request.

![Billing: spend per provider against its limit](shot:billing)

## How the limit works

For each provider and period (total, daily or monthly), a request is refused unless *spent so far + requests in flight + the worst case of this request* stays within the limit. The worst case counts a pessimistic estimate of the input plus the **full** maximum output. The request is clamped to that maximum, so the bound is real.

- Calls are reserved under a lock, so several at once cannot jointly overshoot.
- After the call, the reservation is settled to the usage the provider reports. If none is reported, the worst case is charged.
- An unknown model price is treated as expensive, never free.
- A failed call releases its reservation.

> **Tip:** because the worst case is reserved, a limit is only fully usable if the maximum output is small relative to it. Lower **max output tokens** for a provider if requests are refused even though you have money left.

## Set a limit

1. Open Settings, Model providers. See [Model providers](doc:providers).
2. Pick a provider and set its **limit in dollars** and period.
3. Save. Local models are free and have no limit.

## The Billing page

Shows for each provider the limit, what is spent, what is reserved right now, what remains, the percentage used, number of calls and refusals. External agents and Live sessions appear in their own rows. External agents report their own cost, which is not covered by your limits. See [External agents](doc:external-agents).

## The Dashboard

A summary of the last weeks: spending, activity and what needs attention.

## Account credits

For a provider you can also track the **account credits** you have left. Where the provider's own service reports a balance (OpenRouter does) Agent Studio reads it; otherwise you type the balance in. A request is refused before it is sent if its worst-case cost is more than the credits left, and the page warns when less than a tenth remains.

## Last-resort cloud guard

For a hard stop that does not depend on this PC at all, see the budget kill-switch notes in `docs/gcp-backstop` in the repository.
