---
name: bank-reconciliation
group: Accounting
mcp:
- excel
- xero
description: Match bank-statement lines to ledger entries, list what does not match and propose the fix for each. Use when asked to reconcile an account, find missing or duplicate transactions, or explain why a balance is off.
---

# Bank reconciliation

**When to use:** a bank/card statement (CSV/XLSX) and a ledger export for the same period, and someone needs to know why they differ.

1. Confirm the scope: account, period, opening balance of both sides, currency. Ask for anything missing - never guess a balance.
2. Load both files (read-only). Normalise dates, signs (debit/credit) and amounts to two decimals; keep the original row number for every line.
3. Match in passes, strictest first: exact amount + date + reference; then amount + date within 3 days; then one-to-many (one bank line = several ledger lines, e.g. a batched deposit).
4. List what is left on each side: **in bank not in books** (fees, interest, unrecorded receipts), **in books not in bank** (outstanding cheques, timing), **possible duplicates**.
5. Prove it: ledger balance + outstanding items = bank balance. If it does not tie, say by how much and which passes were loose.
6. Output a table per bucket (date, amount, description, row ref) and a one-line suggested entry for each unmatched item.

**Safety:** this skill only reads and reports. Do not post, edit or delete ledger entries - give the proposed journal lines for a human to approve. Say which assumptions you made (date tolerance, sign convention).
