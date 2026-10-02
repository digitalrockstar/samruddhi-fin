# Changelog

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
