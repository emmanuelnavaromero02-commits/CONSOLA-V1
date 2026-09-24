"""A Postgres schema shaped like SAP Business One, with a deterministic dataset.

This package is the test double for a future `sap_b1` cartridge. It has no
client data and no client names: companies are aliases (`mx_mfg`,
`mx_dist_a`, `mx_dist_b`), business partners and items are synthetic, and
every amount is produced by the generator with a recorded ground truth so a
test can assert exact totals.

Three modules:

* `schema`: the tables (B1 names and column names, B1-like types) and the
  DDL renderer. One schema per company, as in a real HANA tenant.
* `generator`: 24 months of purchases, production, sales, journal entries,
  stock movements and batches for N companies, deterministic by seed, with
  intercompany sales mirrored as the distributors' purchases.
* `loader`: applies the DDL and bulk-loads the dataset into a Postgres DSN.

Dialect note: this is Postgres, not HANA. Identifiers are double-quoted and
case-sensitive like HANA, dates are TIMESTAMP(0) like B1 on HANA, and amounts
are NUMERIC(19,6). `TOP`, `ADD_DAYS`, `IFNULL` and `||` are HANA-only and must
be validated against a real HANA before shipping any extraction SQL.
"""
