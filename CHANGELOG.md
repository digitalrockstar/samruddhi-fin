# Changelog

## Unreleased (v2-templates)
- Messages no parser rule recognises are no longer booked as transactions. They wait on the
  new "SMS formats" page. One tap per format (Not a transaction / Debit / Credit / Own transfer,
  plus mode) is remembered in `sms_templates` and applied to every past and future message
  of that format. Decisions can be undone. On the 4 exports: 369 messages, 79 formats to tap.
- Migrations now self-heal a database emptied by hand (leftover enum types, stale alembic_version).
  Only runs when the core `persons` table is missing, so a live database is never touched.
- Parser version bumped so a reprocess re-evaluates old rows; manually edited rows are kept.

## 1.0.0 - 2026-10-02
First stable release. Rule-based SMS parser, one rule per known bank format.

- Login (HTTP Basic) on dashboard and API; mandatory Telegram webhook secret in production.
- Duplicate-delivery race fixed in ingest.
- Parser: loan, telecom and promo rejection; refund notices ignored (bank credit recorded);
  amount, date-range and balance extraction fixes. Eval on 4 SMS exports: false transactions
  821 -> 8, amount accuracy 94.4% -> 96.3%, correct-day dates 90.4% -> 92.0%.
- Accounts master: loans, investments, last 6 digits, balances, net worth summary.
- Reprocess keeps manually edited transactions.
- asyncpg-safe Neon URLs; favicon.

Branches: `main` = stable (what Render deploys). `v2-templates` = template registry work.
