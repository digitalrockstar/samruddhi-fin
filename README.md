# Samruddhi Finance

A fresh, responsive household expense tracker built with Flask, SQLite (local) and PostgreSQL-compatible configuration. The previous implementations remain archived and untouched.

## V1 features
- Monthly overview: income, expenses, net cash flow, category breakdown and recent activity.
- Transactions: income, expense and transfer; add, edit, delete, search and filter.
- Accounts: cash, bank, savings, credit card and other account types.
- Custom income and expense categories.
- CSV preview/import with validation and duplicate detection; CSV export.
- Responsive desktop/mobile layout and four browser-persisted colour themes.
- Amounts stored as integer paise; transfers excluded from income/expense totals.
- Optional single-password access gate via environment variables.

## Run on Windows PowerShell

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
$env:SECRET_KEY = "replace-with-a-long-random-value"
python app.py
```

Open http://127.0.0.1:5000.

Optional environment variables:
- `DATABASE_URL` — defaults to local SQLite at `instance/samruddhi.db`; PostgreSQL URLs are supported.
- `SECRET_KEY` — set a long random value before hosting.
- `APP_PASSWORD` — enables a single-password login gate.
- `PORT` — defaults to 5000.
- `HOST` — defaults to 127.0.0.1. Set to 0.0.0.0 only in an appropriately protected hosting environment.

For a hosted deployment, set both `SECRET_KEY` and `APP_PASSWORD`, use private database credentials, and configure the database as private. This is a single-user password gate, not a full multi-user identity system.

## CSV format

Required headers: `date,type,amount,description,account`. Optional: `category,transfer_to,notes`.

- Date: `YYYY-MM-DD`
- Type: `income`, `expense` or `transfer`
- Amount: decimal currency, e.g. `1250.50`
- Account/category names must match existing entries (case-insensitive).
- Transfers also need an existing, different `transfer_to` account.

Import previews up to 500 rows before saving. Invalid rows and possible duplicates are skipped when confirmed.

## Tests

```powershell
python -m pytest -q
```

Never commit household transaction data, exports, environment files or local database files.
