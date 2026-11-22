---
name: expense-categorization
group: Accounting
mcp:
- excel
description: Categorise a list of expenses or transactions against a chart of accounts, flag unusual ones and produce a summary by category. Use for receipts, card exports, expense reports and bookkeeping clean-up.
---

# Expense categorisation

**When to use:** a list of transactions (card export, expense report, receipts) must be mapped to accounts.

1. Get the chart of accounts (or use a standard one and say so). Note the entity type, country and tax treatment if the user states one - do not assume tax rules.
2. For each line decide: account, confidence (high/medium/low), and the reason in a few words (vendor, description keyword). Recurring vendors get the same account unless the amount pattern changes.
3. Flag, do not hide: duplicates (same vendor, amount, date within 3 days), round-number amounts, weekend/holiday spend, missing receipts, personal-looking items, amounts above a threshold the user sets.
4. Output: the categorised table, a subtotal per account, a list of **needs a human** lines (low confidence or flagged).
5. Never invent a vendor or receipt. If a description is too vague ("misc"), mark it uncategorised and ask.

**Safety:** it classifies; it does not submit, approve or pay anything. Tax-deductibility is a question for the user's accountant - label it as such.
