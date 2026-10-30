# Samruddhi

Household finance tracker (manual entry): income, expenses, transfers, credit cards, loans, budgets, goals, cashback and approvals.
Flask + SQLAlchemy, Postgres (Neon) in production, SQLite locally. Responsive web app, installable as a PWA, wrappable as an APK (see `android/`).

**Deploy:** see [DEPLOY.md](DEPLOY.md) · **Tests:** `python -m pytest tests -q`

## Rules the app enforces (all covered by tests)
- Zero-sum: balance = starting balance + posted income − expenses ± transfers. Only **posted** items dated today or earlier count; pending/rejected never do.
- Credit cards and loans hold what is owed. Add-on cards share the parent's limit. Debit cards and UPI move their linked bank account.
- Spending is blocked beyond balance or credit limit. Loan repayments cannot exceed what is outstanding.
- Subscriptions, cashback, goal assignments and bill reminders are auto-created as **pending** and need admin approval.
- Cashback: percent or fixed, minimum spend, cap, delay, one-time, payout account; or one-off on a transaction.
- Bill reminders: approving creates the transfer; loan EMIs split into principal (reduces the loan) and interest (an expense).
- Goals earmark money in bank/investment accounts only; balances never change. If a withdrawal leaves an account holding less than is earmarked, the withdrawal goes through, a warning is shown, and goals are reduced with negative entries.
- Budgets: Family expenses count against family budgets; Individual expenses count against each person's budget, split equally or by chosen amounts.
- Accounts and categories are archived, never deleted. Temporary categories can have a date window.
- Roles live in `samruddhi/permissions.py` (admin, member). Minors are tags and cannot sign in.
- Data reset: full, all transactions, or from a date, each behind password + typed confirmation, with CSV backup.

## Layout
`samruddhi/ledger.py` all business rules · `routes_*.py` thin web layer · `templates/` + `static/` UI (themes in `static/css/app.css`) · `android/` WebView shell (PNG launcher icons, regenerate with `scripts/gen_icons.py`) · `scripts/gen_themes.py` builds the 42 themes · `_legacy/` previous versions, untouched.
