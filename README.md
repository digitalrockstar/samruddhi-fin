# Samruddhi Finance

A responsive household expense tracker built with Flask, SQLAlchemy, SQLite for local use, and PostgreSQL-compatible configuration. The app is designed for **one family workspace per deployment**, with a separate login for each family member.

## Repository boundaries

The _legacy/ tree and secrets/ are off-limits. Do not read or print secrets, import legacy code/config, or edit, move, or delete archived files.

## Current features

- Household dashboard with income, expenses, approved cashback, net cash flow, category breakdown and recent activity.
- Individual member logins with password hashes, first-admin setup, admin-managed membership, and shared family visibility.
- Accounts mapped to an individual owner (payer by default) or the shared family; optional purpose tags such as Savings, Emergency Fund, Credit, Wallet and Bills.
- Transactions for income, expense and transfer, with add/edit/archive/restore, search/filter, optional beneficiary attribution, equal/amount/percentage splits, and one selected budget context.
- L1/L2 category hierarchy, for example Grocery → Zepto and Cab → Uber.
- Monthly and annual budgets, family or individual ownership, category targeting, and optional even monthly allocation of an annual target.
- Family or individual goals. Earmarking existing balances toward a goal is neutral: it does not move account money or create an expense. The same amount cannot be earmarked across goals beyond the available account balance.
- Subscriptions/recurring expense templates: weekly, monthly, annual or every N weeks (e.g. every 6 weeks). Each due occurrence becomes a new transaction when the app is opened. Editing a subscription's upcoming amount affects future occurrences only; history is not rewritten.
- Card cashback schemes matched by card and merchant keyword; the expected amount and destination are recorded, then placed into an approval queue on the expected date. Only approval posts the reward. Actual reward amount can be corrected at approval.
- Credit-card individual/shared limits, utilization, statement tracking, linked bank-to-card payments, and in-app due-date reminders.
- CSV preview/import with validation and duplicate detection; CSV export.
- Responsive desktop/mobile layout and four browser-persisted colour themes.
- Currency entry and stored money values are INR/paise only. Multi-currency travel transactions and upfront foreign-exchange conversion are planned for V2.

## First run on Windows PowerShell

    py -3.12 -m venv .venv
    .\.venv\Scripts\Activate.ps1
    python -m pip install -r requirements.txt
    $env:SECRET_KEY = "replace-with-a-long-random-value"
    python app.py

Open http://127.0.0.1:5000. On first run, the app redirects to **Set up your family workspace**. Create the first administrator login. Then use **People** to add each family member with a separate email and initial password. Share initial passwords securely outside the app; each member can change their password after signing in. Email invitations and password-reset email are not configured.

## Environment variables

- DATABASE_URL — defaults to local SQLite at instance/samruddhi.db; PostgreSQL URLs are supported.
- SECRET_KEY — set a long random value for hosting.
- PORT — defaults to 5000.
- HOST — defaults to 127.0.0.1. Set to 0.0.0.0 only in an appropriately protected hosting environment.

Use HTTPS in production, secure database credentials, and persistent database storage. Passwords are stored as one-way hashes. This release is intended for one household workspace per deployment; it does not yet provide multi-household tenant isolation or a password-reset email flow.

## Family members and permissions

Every member has an individual email/password login and can change their own password. Data is shared within the deployed household workspace. An administrator can create or deactivate member logins, assign account ownership, and manage cashback schemes. Members can enter transactions and create budgets/goals for the shared household workspace. Existing accounts are initially treated as shared until an administrator assigns their owner; this avoids guessing historical payers. Account ownership drives the default payer for new transactions, and that payer is snapshotted so future ownership changes do not rewrite prior attribution. Beneficiary splits are only for spending attribution and never create money owed between members.

## Budgets

A transaction can be assigned to one active budget, or left in the family context without a named budget. Family and individual budgets are separate; one transaction cannot count against both. Monthly budgets track the current month. Annual budgets track the calendar year. Checking **Split annual target into 12 equal monthly allocations** also shows the monthly allocation and current-month usage.

For a category-specific budget, choose a transaction category within that category/subcategory. Amount-based beneficiary allocations must sum exactly to the transaction amount; percentage allocations must sum to 100%.

## Goals

Goals are owned by either the family or one member. Goal earmarks designate existing balances in non-credit accounts. They are not transfers, expenses, or extra cash. The available account balance is checked before earmarking, and the same account value cannot be earmarked beyond its balance across several goals.

## Subscriptions

An expense can be marked as a subscription at entry. The form schedules the next occurrence from that transaction's date. For manually created subscriptions, set the next due date directly. Supported recurrence is weekly, monthly, annual, or every N weeks (1–52).

Due occurrences are materialized when an authenticated page is opened; there is no background worker, so generation can be delayed while the app is not opened. If multiple due dates were missed, the app catches up in chronological order. Editing the amount or next date in Subscriptions changes the template only; already-recorded transactions remain unchanged. Variable renewals such as Microsoft OneDrive can be adjusted before the next annual charge.

## Cashback and approvals

Create schemes under **Cashback schemes**, selecting the credit card, merchant keyword, rate, delay and destination account. A purchase description containing the keyword can auto-match the longest matching rule for that card; the transaction form also permits a manual rate/destination. Expected cashback never affects account balances before approval.

When an event reaches its expected date, it appears under **Approvals**. Approve it with the amount actually credited, or mark it not received for later review. Wallet/bank credits increase that account; cashback credited to a credit-card account reduces card debt. Rewards appear separately from normal income. Approving an event posts it once, and edits to an original purchase with an approved cashback are blocked to preserve history. Archive/restore of the original purchase also archives/restores its posted reward.

Reminder and subscription processing occurs when the app is opened. Email/Telegram notifications and background scheduling are not part of this release.

## Credit cards

Create credit card accounts under **Accounts**, then configure each card under **Credit cards**. A card can have its own limit or belong to a shared limit group; shared utilization and available credit are aggregated without counting a group limit more than once.

Add the bank-issued statement month/date, due date, total due and minimum due. Recording a payment creates a bank/cash/savings-to-card transfer linked to the statement. Transfers and credit-card bill payments never create a second expense. Statement reminders appear two days before, on the due date, and while overdue when the app is opened.

## CSV format

Required headers: date,type,amount,description,account. Optional: category,transfer_to,notes.

- Date: YYYY-MM-DD
- Type: income, expense or transfer
- Amount: decimal INR, e.g. 1250.50
- Account/category names must match existing entries (case-insensitive).
- Transfers also need an existing, different transfer_to account.

Import previews up to 500 rows before saving. Invalid rows and possible duplicates are skipped when confirmed.

## Tests

    python -m pytest tests -q

Never commit household transaction data, exports, environment files or local database files.
