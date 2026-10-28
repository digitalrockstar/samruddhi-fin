# Samruddhi Fin

Household expense tracker for two people. Bank SMS are forwarded into a Telegram
group chat; a bot webhook parses them into structured transactions and this app
categorises, splits and reports on them.

```
Telegram ──webhook──▶ raw_messages ──▶ parse ──▶ categorise ──▶ transactions
                      (kept forever)     │                       (derived view)
                                          └─ parser_version stamped per row
                                             └─ POST /api/raw/reprocess
                                                re-derives everything
```

**`raw_messages` is the source of truth; `transactions` is a derived view.**

Every inbound message is archived verbatim *before* parsing — including OTPs,
declined cards, promos, and anything the parser rejects. That buys three things:

1. **The parser stays improvable.** Change a rule, bump `PARSER_VERSION`, then
   `POST /api/raw/reprocess` rebuilds every transaction. No re-forwarding.
2. **Rejections are explainable.** "Why isn't this showing up?" → find the raw
   row and read its `reason`.
3. **Nothing is lost.** A message that failed to parse today can be revived.

The parser is a pure function, so it can also be re-run offline against the
archive and scored (`scripts/eval_parser.py`).

### Why a webhook rather than `getUpdates` polling

They are mutually exclusive — `getUpdates` returns nothing while a webhook is
registered. For this deployment the webhook wins:

| | Webhook | `getUpdates` |
|---|---|---|
| Latency | instant | up to the poll interval |
| Render free tier | wakes on request | **sleeps after 15 min idle** |
| Downtime | Telegram retries, queues ~24h | **updates expire after 24h** |
| Public URL needed | yes (Render provides one) | no |

Polling would win if there were no public URL, or if batch-on-a-schedule were
wanted. There is one, so polling would mean a paid always-on worker plus a
silent data-loss window.

## Message format

Forward the SMS into the group chat. The person is worked out from the shape of
the forward:

| Forward looks like | Attributed to |
|---|---|
| `AP- <sms>` or `AP: <sms>` | Akshay Patel |
| `AS- <sms>` or `AS: <sms>` | Ashka Shah |
| `AS: New SMS: <sms>` | Ashka Shah |
| `From: +91…` / `Time: 14:32` / `<sms>` | Akshay Patel |

```
AP- ICICI: Rs. 2,499.00 spent on Amazon Seller Services VISA Card XX4321 on 05-08-2025 21:10
AS: New SMS: Rs.120.00 from Kotak Bank AC X0359 to SWIGGY@paytm on 04-07-24

From: +919812345678
Time: 14:32
HDFC Bank: Rs. 1,250.50 debited from A/C XXXXXX8899 via UPI to SWIGGY on 12-08-2025
```

Details:

- `From:` / `Time:` header lines are stripped before parsing, so the phone number
  in the header can never be mistaken for an amount or an account.
- A `Time: 14:32` header is used as the clock time when the SMS body has a date
  but no time of day. A time already in the body always wins.
- Share-sheet wrappers (`New SMS:`, `New Message:`, `Forwarded message:`) are
  stripped wherever they appear.
- A message with **no** attribution at all (e.g. bare `New SMS: …`) is rejected,
  rather than silently charged to the default person.
- Duplicate `message_id`s are skipped, so Telegram retries are safe.

Adding a third person: add one entry to `ALIASES` in
`app/services/telegram.py`, and set `DEFAULT_PREFIX` if their forwards are the
un-prefixed kind.

## Concepts

- **Paid By** - worked out from the forward's shape (see Message format),
  stored on the transaction.
- **Expense For / Income From** - who the money was actually *for*. Defaults to
  the payer, editable afterwards. Leaving a row with no person means
  **Home (Both)**, i.e. shared.
- **Splits** - one transaction can carry several allocations, e.g. an Amazon
  order split 60/40 between Akshay and Ashka. This is what powers "my wife's
  card, my purchase".
- **Transfers** - credit card payments, account-to-account moves. Categorised
  under `TRANSFER` so they reduce card outstanding without being counted as
  household spending.
- **Cashback** - every card carries a default percentage/type/wallet, which
  pre-fills each transaction; any field can be overridden per transaction and
  tracked as pending → received.
- **Merchant learning** - assigning a category by hand writes a merchant rule,
  so the next identical merchant auto-categorises.
- **Spam** - OTP/promo/verification SMS are detected and routed to `IGNORE`.

## Categories

Four roots, each with seeded subcategories:

| Root        | Purpose                                    |
|-------------|--------------------------------------------|
| `EXPENSE`   | Things you spend                          |
| `INCOME`    | Things you receive                        |
| `TRANSFER`  | Movements between own accounts/cards      |
| `IGNORE`    | Spam, OTPs, promos - excluded from totals  |

Subcategories are editable under **Settings → Categories**. A new subcategory
inherits its parent's type.

## Running locally

```bash
python -m venv .venv
.venv\Scripts\activate            # Windows
pip install -r requirements.txt
copy .env.example .env            # set DATABASE_URL
alembic upgrade head              # create/refresh the schema
uvicorn app.main:app --reload
```

For a zero-setup local run, point `DATABASE_URL` at SQLite:

```
DATABASE_URL=sqlite+aiosqlite:///./local.db
```

Reference data (2 persons + the category tree) is seeded on every boot and is
idempotent. Schema changes are **not** applied automatically - the app refuses
to start if the schema is missing, so run the migration first.

## Database migrations

Alembic owns the schema. There is no `create_all` in the app.

```bash
alembic upgrade head          # apply
alembic downgrade -1          # roll back one revision
alembic current               # what is applied
alembic history               # revision graph

# after editing app/models/__init__.py
alembic revision --autogenerate -m "add widget column"
alembic upgrade head
alembic downgrade -1          # sanity-check the downgrade path
```

`alembic/env.py` reads `DATABASE_URL` from `app.config`, so migrations and the
app can never drift to different databases. It also accepts an explicit
`sqlalchemy.url` override (used by tests) and handles both async and sync
drivers.

`tests/test_migrations.py` upgrades a scratch database and then asserts
autogenerate reports **zero** differences from the models, so forgetting to
generate a revision fails CI instead of production.

Review SQL without touching a database:

```bash
alembic upgrade head --sql    # prints the Postgres statements
```

## Deploying to Render

Set **three** variables and deploy:

| Key | Value |
|---|---|
| `DATABASE_URL` | Neon **pooled** connection string (must contain `-pooler`) |
| `TELEGRAM_BOT_TOKEN` | your new token from @BotFather |
| `TELEGRAM_CHAT_ID` | `-1004399659853` |

`SECRET_KEY` is generated by Render; `PYTHON_VERSION` and `APP_ENV` are fixed in
`render.yaml`. That is everything.

```bash
# 1. Push to Git, then create a Web Service in Render from the repo.
# 2. Paste the three variables above.
# 3. Deploy. Watch the logs for:
#        Running upgrade  -> b6f72f816833, initial schema
#        Uvicorn running on http://0.0.0.0:10000

# 4. Register the Telegram webhook (run this LOCALLY, not on Render):
#    WEBHOOK_URL=https://<your-service>/webhook/telegram
#    python -m scripts/setup_webhook

# 5. Add your cards under Settings -> Accounts.
```

Verify:

```bash
curl https://<your-service>/health            # {"status":"healthy",...}
curl https://<your-service>/webhook/telegram  # telegram_enabled: true
```

### Migrations run at start, not as a pre-deploy step

```
startCommand: alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port $PORT
```

Render's `preDeployCommand` is a **paid-plan** feature. On the free plan it is
silently skipped, leaving an empty database that the app correctly refuses to
use - a confusing first-deploy failure. Running migrations first in
`startCommand` works on every plan, and `&&` means the app only boots once the
schema is current.

## Pages

| Route             | Purpose                                                     |
|-------------------|-------------------------------------------------------------|
| `/`               | Dashboard: totals, averages, category/mode/person breakdown, daily + monthly charts, cashback, recurring |
| `/transactions`   | Filterable list, categorise, split, cashback editor, CSV export |
| `/uncategorized`  | Review queue for transactions the parser could not categorise, with bulk assign |
| `/settings`       | Cards/accounts, categories, merchant rules, recurring, cashback |

There is no authentication. The person selector in the header (persisted in
`localStorage`) is used as the "who am I" context for filtering and for future
budget alerts - it is not a security boundary.

## API

```
GET    /webhook/telegram                    Telegram bot webhook
GET    /webhook/telegram                    webhook status

GET    /api/transactions                    list + filter + paginate
GET    /api/transactions/uncategorized      review queue
GET    /api/transactions/export/csv         CSV export (same filters)
POST   /api/transactions/bulk-categorize    bulk assign
GET    /api/transactions/{id}
PATCH  /api/transactions/{id}               category, cashback, allocations
DELETE /api/transactions/{id}

GET    /api/categories/tree                 full nested tree
GET    /api/categories                      roots (or ?parent_id=)
POST   /api/categories                      create subcategory
PATCH  /api/categories/{id}
DELETE /api/categories/{id}
POST   /api/categories/reorder

GET    /api/accounts   POST /api/accounts
PATCH  /api/accounts/{id}   DELETE /api/accounts/{id}   (soft if in use)

GET    /api/persons    POST /api/persons    PATCH /api/persons/{id}

GET    /api/mappings   POST /api/mappings   DELETE /api/mappings/{id}

# Raw archive - every inbound message, before any parsing
GET    /api/raw?verdict=&reason=&prefix=&stale_only=&unlinked=
GET    /api/raw/stats                     what is archived, what is stale
GET    /api/raw/{id}
POST   /api/raw                           ingest manually (missed an SMS)
POST   /api/raw/reprocess                 re-derive transactions (dry_run supported)
DELETE /api/raw/{id}?delete_txn=

GET    /api/dashboard/summary?period=day|week|month|year|all
GET    /api/dashboard/averages?period=...
GET    /api/dashboard/trend?grain=day|week|month&buckets=30
GET    /api/dashboard/cashback?months=6
GET    /api/dashboard/recurring?days_ahead=30

GET    /health
```

Money is serialised as a JSON string to preserve decimal precision; the
frontend parses with `Number()`.

## Tests

```bash
python -m pytest tests -q
```

- `test_raw_archive.py` - the archive stores rejections too, re-delivery is a no-op, `reprocess` is read-only when dry-running and never duplicates transactions.
- `test_prefixes.py` - the four forwarding shapes, header stripping, time hints, attribution precedence.
- `test_parser.py` - real bank formats verbatim from your SMS exports, plus date/account/merchant edge cases and the not-a-transaction cases.
- `test_ingest.py` - prefixed message → transaction, allocations, transfers, spam/scheduled/declined rejection, merchant learning.
- `test_api_flow.py` - full HTTP flow against a **real Alembic-migrated** database: accounts, webhook idempotency, splits, cashback, filters, CSV export.
- `test_migrations.py` - upgrade/downgrade roundtrip, model/migration parity, enum storage, the boot guard, category uniqueness.
- `test_dashboard_sql.py` - dashboard queries compile to valid PostgreSQL.

Dashboard date bucketing uses `date_trunc` on PostgreSQL and `strftime` elsewhere,
so the suite also runs on SQLite.

---

## Working with the SMS exports

If you have SMS Backup & Restore XML exports, these tools tune and measure the
parser against real messages:

```bash
# 1. See what senders exist and what shapes each one uses
python -m scripts.analyze_sms "C:/path/to/sms/*.xml" --out report.txt

# 2. Build a labelled corpus (posted vs not-posted)
python -m scripts.build_corpus "C:/path/to/sms" --out corpus.jsonl

# 3. Score the parser against it
python -m scripts.eval_parser corpus.jsonl
```

After changing `app/services/parser.py`, re-run step 3 and compare. Measured on
29,394 real messages:

| Metric | Before real-data tuning | Now |
|---|---|---|
| amount extracted correctly | 98.7% | 94.6% |
| transaction date correct (±1 day) | 94.6% | 90.5% |
| merchant identified | 80.3% | 78.0% |
| account last-4 identified | 83.7% | 83.7% |
| **non-transactions correctly rejected** | **63.1%** | **92.1%** |

The amount figure moved down deliberately: the original parser grabbed amounts
out of messages that were not transactions at all (declined cards, future autopay
schedules, voucher balances), which is worse than missing one.

### Backfilling history

```bash
# dry run first - prints a sample of what would be imported
python -m scripts.backfill "C:/path/to/sms/*.xml" --prefix AS- --dry-run

# then for real
python -m scripts.backfill "C:/path/to/sms/*.xml" --prefix AS-
```

On the sample exports this finds 8,901 importable transactions and skips 8,825
non-transactions. It is idempotent (`message_id` is derived from the SMS
timestamp plus a body hash), so re-running is safe.

To pre-seed merchant categories so the backfill auto-categorises:

```json
// mappings.json
{
  "swiggy":   { "root": "EXPENSE", "category": "Food Delivery" },
  "zomato":   { "root": "EXPENSE", "category": "Food Delivery" },
  "amazon":   { "root": "EXPENSE", "category": "Amazon" },
  "torrent power": { "root": "EXPENSE", "category": "Electricity" }
}
```

```bash
python -m scripts.backfill "*.xml" --prefix AP- --mappings mappings.json
```

The backfill archives to `raw_messages` exactly like the webhook, so after a
parser improvement you can re-derive your whole history without re-exporting
anything:

```bash
# see what would change
curl -X POST localhost:8000/api/raw/reprocess \
  -H 'Content-Type: application/json' \
  -d '{"stale_only": true, "dry_run": true}'

# apply it
curl -X POST localhost:8000/api/raw/reprocess \
  -H 'Content-Type: application/json' -d '{"stale_only": true}'
```

Bump `PARSER_VERSION` in `app/services/telegram.py` whenever parsing behaviour
changes — that is what marks archived rows stale.

### Parser behaviour on real messages

Messages that are **rejected** (no transaction is created):

| Reason | Examples |
|---|---|
| `otp` | `862585 is the OTP for transaction of INR 30043.71...` |
| `declined` | `Trxn ... could not be approved`, `Auto-Pay ... failed as` |
| `scheduled` | `AutoPay for Vi Postpaid bill of INR 588.82 is scheduled on 07-07-2024` |
| `balance` | `Avl bal in your Kotak A/c XXXX1832 ... is INR 480807.69` |
| `statement` | `Total due amt: ... Min due amt: ...`, `Reminder: Payment for card ... is due` |
| `promo` | offers, pre-approved loans, telecom plan marketing |
| `envelope` | voucher balances, corrupted text |

Messages that become **transfers** (so they do not inflate household spending):

| Trigger | Example |
|---|---|
| `cheq.payu@*` handle | `Sent Rs.31664.00 ... to cheq.payu@icici` → Credit Card Payment |
| `gpay-*` handle | `Sent Rs.70.00 ... to gpay-11240878578@okbizaxis` → Wallet Top-up |
| card payment received | `PAYMENT OF Rs. 15798.00 RECEIVED TOWARDS YOUR CREDIT CARD ENDING 0030` |
| `Payment of ... is credited to your ... Credit Card` | Kotak/HDFC card top-up |

Manual parser check on any string:

```bash
python -m scripts.try_parser "AP- Rs 500 debited via UPI to SWIGGY on 12-08-2025 14:32"
```

## Layout

```
app/
  main.py           FastAPI app + page routes
  config.py         env settings (normalises the DB URL)
  database.py       async engine/session + schema check
  models/           SQLAlchemy models
  schemas/          Pydantic request/response models
  services/
    parser.py       SMS → ParsedSMS (pure; re-runnable against the archive)
    categorizer.py  learned rules + keyword heuristics + recurring detection
    telegram.py     ingest_raw / derive / preview, PARSER_VERSION
    dashboard.py    aggregation queries
    seed.py         idempotent reference data
  api/              routers (incl. raw.py: archive + reprocess)
  templates/        Jinja2 + Alpine (server-rendered shells)
alembic/            migrations (env.py reads app settings)
static/             css / js
scripts/            doctor, telegram_info, setup_webhook, demo, backfill,
                    analyze_sms, build_corpus, eval_parser, try_parser
tests/
```

See **[CONFIGURATION.md](CONFIGURATION.md)** for every environment variable,
where it is read, and what to change per environment.

## Notes / limitations

- Enum columns store enum **names** (`EXPENSE`), and both enum members and raw
  strings coerce to the same value. Locked in by `test_migrations.py`.
- Category uniqueness relies on two partial unique indexes rather than a single
  `UNIQUE(name, parent_id)`, because Postgres treats NULLs as distinct and would
  otherwise allow duplicate root categories.
- `category_id` is a single leaf category per transaction. An `expense`/`income`
  split by *multiple* categories is not modelled.
- A household transfer between your own accounts appears as **two** transactions
  (one debit, one credit), which is correct per-person but means it shows up in
  both people's totals. If you want those collapsed, that needs a linking step.
- `transactions.message_id` still mirrors the Telegram message id for backwards
  compatibility, but `raw_messages.transaction_id` is the authoritative link.
- The "who's accessing" selector is client-side only; anyone with the URL can
  edit anything. Add auth before exposing this beyond the two of you.
- Budget alerts into the Telegram group are not implemented yet - the
  `/api/dashboard/*` endpoints and `RecurringTransaction` table are the hooks.