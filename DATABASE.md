# Adding a real database later

Right now all payment data lives in `ledger.csv`, and every other part of the
app only ever touches it through `ledger_store.py`'s five functions:
`load_all()`, `get(id)`, `update(id, fields)`, `append(row)`,
`known_transaction_ids()`. `app.py` (the API) and `extractor.py` (OCR) don't
know or care that storage is a CSV — that's the whole point of the seam.
This doc is the plan for when you're ready to replace it.

## Reach for SQLite first, not Postgres/MySQL

For a single-user local tool, SQLite is the fast path — not a stepping stone
you'll outgrow in a week:

- **Zero setup.** `sqlite3` is in the Python standard library. No server to
  install, no daemon to keep running, no credentials.
- **Still 100% local.** Keeps the app's "no cloud, no API keys" property
  exactly as it is today — a `payledger.db` file instead of `ledger.csv`.
- **Actually fast.** Today, `get()`/`update()`/`append()` each read and
  rewrite the *entire* CSV, and `known_transaction_ids()` does a full scan.
  Fine at hundreds of rows; wasteful in the thousands. SQLite with an index
  on `transaction_id` turns the hottest path (duplicate detection on every
  upload) into an indexed lookup instead of an O(n) scan.
- **Safe concurrent access.** Turn on WAL mode (below) and reads/writes from
  the Flask dev server don't step on each other, even with multiple browser
  tabs open.
- **A portable schema.** If you ever genuinely need Postgres/MySQL later
  (see "When to go further" below), the table design carries over — you're
  not throwing this away, you're swapping the driver underneath it.

Only jump straight to Postgres/MySQL if you already know you need networked,
multi-writer access (e.g. this app running on two machines against the same
live database at once). For "I use this on my own machine, maybe two
machines at different times," SQLite is both faster to build and faster to
run.

## What changes, what doesn't

- **Untouched:** `app.py`, `extractor.py`, `static/*`, `requirements.txt`
  (sqlite3 ships with Python — no new dependency).
- **Changed:** only the internals of `ledger_store.py`. Every function keeps
  its exact name, arguments, and return shape (`list[dict]` /
  `dict | None` / `bool`), so nothing upstream needs to know it happened.

## Migration steps

**1. Schema** (put this in a `schema.sql` or run it once at startup with
`CREATE TABLE IF NOT EXISTS`):

```sql
CREATE TABLE IF NOT EXISTS payments (
  id              TEXT PRIMARY KEY,
  file            TEXT,
  app             TEXT,
  amount          TEXT,
  date_time       TEXT,
  transaction_id  TEXT,
  party           TEXT,
  direction       TEXT,
  status          TEXT,
  ocr_confidence  REAL,
  needs_review    INTEGER,   -- 0/1
  review_reasons  TEXT,
  notes           TEXT,
  file_path       TEXT,
  processed_at    TEXT
);
CREATE INDEX IF NOT EXISTS idx_payments_txn ON payments(transaction_id);
```

**2. Rewrite `ledger_store.py` internals**, same public functions:

```python
import sqlite3
from pathlib import Path

DB = Path(__file__).parent / "payledger.db"

def _connect():
    conn = sqlite3.connect(DB)
    conn.execute("PRAGMA journal_mode=WAL")   # smooth concurrent reads/writes
    conn.row_factory = sqlite3.Row
    return conn

def load_all() -> list[dict]:
    with _connect() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM payments")]

def get(payment_id: str) -> dict | None:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM payments WHERE id = ?", (payment_id,)).fetchone()
        return dict(row) if row else None

def update(payment_id: str, fields: dict) -> bool:
    if not fields:
        return get(payment_id) is not None
    with _connect() as conn:
        cols = ", ".join(f"{k} = ?" for k in fields)
        cur = conn.execute(f"UPDATE payments SET {cols} WHERE id = ?", (*fields.values(), payment_id))
        return cur.rowcount > 0

def append(row: dict) -> dict:
    row = {**row, "id": row.get("id") or uuid.uuid4().hex}
    with _connect() as conn:
        cols = ", ".join(row)
        placeholders = ", ".join("?" for _ in row)
        conn.execute(f"INSERT INTO payments ({cols}) VALUES ({placeholders})", tuple(row.values()))
    return row

def known_transaction_ids() -> set:
    with _connect() as conn:
        rows = conn.execute("SELECT DISTINCT transaction_id FROM payments WHERE transaction_id != ''")
        return {r[0] for r in rows}
```

(`needs_review` becomes `0`/`1` in SQLite — convert to/from `bool` at the
edges, the same way `_load_df()` does today for the CSV's `"True"`/`"False"`
strings.)

**3. One-time import** — a script you run once, not part of the app:

```python
import csv, sqlite3
conn = sqlite3.connect("payledger.db")
conn.executescript(open("schema.sql").read())
with open("ledger.csv") as f:
    for row in csv.DictReader(f):
        cols = ", ".join(row)
        placeholders = ", ".join("?" for _ in row)
        conn.execute(f"INSERT INTO payments ({cols}) VALUES ({placeholders})", tuple(row.values()))
conn.commit()
```

Keep `ledger.csv` around afterward as a plain-text backup — don't delete it
the same day you migrate.

**4. Test** using the same checks as today: list view loads, detail view
opens, edit + save persists, upload processes and skips duplicates, export
still produces a valid `.xlsx`. None of the API routes or frontend code
change, so this is really just verifying `ledger_store.py`'s new internals
against its own existing contract.

## Performance notes for when it matters

- The `transaction_id` index above is the one that actually matters — it's
  hit on every single upload for duplicate detection.
- Reuse one connection per process instead of connecting per request if this
  ever moves off the Flask dev server (store it on `app.config` or `flask.g`).
- If you ever bulk-import many screenshots at once, wrap the whole batch in
  a single `conn.execute("BEGIN")` / commit instead of one commit per row.

## When to go further than SQLite

Only when one of these is actually true — not preemptively:

- You need real concurrent writes from multiple machines against one live
  database at the same time (not "I use it on two laptops on different days").
- Something other than this app needs to query the same data.
- Your data volume genuinely won't fit comfortably on one machine (a
  personal payment ledger will not hit this in practice).

If that day comes, Postgres/MySQL + SQLAlchemy is the natural next hop, and
because `ledger_store.py` is still the only thing that talks to storage,
it's a contained swap — the same reason this whole seam exists in the first
place.
