---
name: month-end-close-checklist
group: Accounting
mcp:
- excel
- aws-billing
description: 'Run a month-end close checklist: confirm each step, list open items with an owner and produce a close status summary. Use when preparing or reviewing a period close.'
---

# Month-end close

**When to use:** closing a monthly (or quarterly) period, or checking how ready a close is.

Work through the list and mark each item **done / open / blocked / not applicable**, with evidence (file, report, who confirmed):

1. Bank and card accounts reconciled (see the bank-reconciliation skill).
2. Accounts receivable and payable ageing reviewed; unapplied payments cleared.
3. Accruals and prepayments posted (rent, payroll, utilities, subscriptions); recurring journals run.
4. Fixed-asset additions and depreciation recorded.
5. Payroll and payroll-tax liabilities agree to the payroll report.
6. Inventory/stock count and cost-of-goods reviewed (if applicable).
7. Sales tax / VAT liability agrees to the return working.
8. Intercompany and loan balances agree to statements.
9. Trial balance reviewed: unusual movements vs last month and vs budget explained (use a variance table, flag > 10 % and > a user-set amount).
10. Period locked only after a human signs off.

Output a status table, the open items sorted by impact, and the questions that block closing. Do not post journals or lock the period yourself.

**Safety:** read-only review. Never post journals, change balances or lock the period yourself - a human signs off.
