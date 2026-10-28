# Samruddhi Fin — Initial Product Brief

## Purpose
Private personal/household expense tracker. The app is the first managed product; the Build Console is a separate control-plane.

## Proposed V1 scope
- Dashboard with selected-month income, expenses, net cash flow and category breakdown.
- Transactions: income, expense, transfer; create/edit/archive; search, sort and filter.
- Accounts: cash, bank, savings and credit accounts with transfers.
- Customizable categories and sensible defaults.
- Monthly category budgets and utilization warnings.
- Reports by month, category and account.
- CSV import with preview, validation and likely-duplicate detection; CSV export.
- Responsive phone, tablet and desktop UI.
- INR default; configurable currency/date settings.
- Authentication and private data access if cloud sync is selected.

## Money and ledger rules
- Avoid binary floating-point for money: use integer minor units or exact decimals and document the choice.
- Transfers between owned accounts alter account balances but do not count as income or expense.
- Decide credit-card purchase/payment semantics before implementation.
- CSV import must preview and validate before committing, with possible duplicate detection.
- Use synthetic test data. Never use actual personal transactions in development or screenshots.
- No AI features or bank integrations in V1 unless explicitly added.

## Questions for creator
1. Local-first single device or cloud sync across devices?
2. Single user or shared household members/accounts?
3. Account types for V1: cash, bank, savings, credit cards, investments?
4. Receipt attachments in V1?
5. Is CSV enough? Which formats are required?
6. In-app budget alerts only or push/email?
7. Is offline operation required?
8. Split transactions and credit-card statement/payment reconciliation?

The product-intelligence agent must distinguish assumptions from approved decisions. Do not silently elevate optional features into scope.
