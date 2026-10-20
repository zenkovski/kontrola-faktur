"""SQLite store. The database, not the code, guarantees one live invoice per supplier and number.

Keys:
- content_hash: the exact same document again -> replay (return old result, create nothing).
- (supplier_ico, number_key): same number written any way ("FV-2026/0458" = "fv 2026 0458"),
  different content -> duplicate / tampering. Enforced by a partial UNIQUE index.
- invoice_lines: what each non-stopped invoice billed, so a second invoice for the same order
  cannot bill goods again.
Every row keeps the decision, the reasons, where the data came from and when: the audit trail.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import asdict
from decimal import Decimal

from .models import Invoice, number_key

SCHEMA = """
CREATE TABLE IF NOT EXISTS invoices (
    id             INTEGER PRIMARY KEY,
    content_hash   TEXT NOT NULL UNIQUE,
    supplier_ico   TEXT NOT NULL,
    invoice_number TEXT NOT NULL,
    number_key     TEXT,
    issue_date     TEXT NOT NULL,
    total          TEXT NOT NULL,
    po_number      TEXT,
    decision       TEXT NOT NULL,
    findings_json  TEXT NOT NULL,
    source         TEXT,
    rules_version  TEXT,
    created_at     TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS invoice_lines (
    invoice_id INTEGER NOT NULL REFERENCES invoices(id),
    po_number  TEXT,
    sku        TEXT NOT NULL,
    qty        TEXT NOT NULL
);
"""
INDEXES = """
DROP INDEX IF EXISTS one_live_number;
CREATE UNIQUE INDEX IF NOT EXISTS one_live_number_key
    ON invoices (supplier_ico, number_key) WHERE decision != 'STOP';
CREATE INDEX IF NOT EXISTS lines_by_po ON invoice_lines (po_number, sku);
"""
NEW_COLUMNS = {"number_key": "TEXT", "po_number": "TEXT", "source": "TEXT", "rules_version": "TEXT"}


def content_hash(inv: Invoice) -> str:
    data = json.dumps(asdict(inv), sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha256(data.encode()).hexdigest()


class Store:
    def __init__(self, path: str = ":memory:"):
        # web server threads share one connection; the caller serialises access with a lock
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self._migrate()
        self.db.executescript(INDEXES)

    def _migrate(self) -> None:
        """Older history files lack some columns. Add them and fill the duplicate key."""
        have = {r["name"] for r in self.db.execute("PRAGMA table_info(invoices)")}
        for col, typ in NEW_COLUMNS.items():
            if col not in have:
                self.db.execute(f"ALTER TABLE invoices ADD COLUMN {col} {typ}")
        for row in self.db.execute("SELECT id, invoice_number FROM invoices WHERE number_key IS NULL").fetchall():
            self.db.execute("UPDATE invoices SET number_key = ? WHERE id = ?",
                            (number_key(row["invoice_number"]), row["id"]))
        self.db.commit()

    def by_hash(self, h: str) -> sqlite3.Row | None:
        return self.db.execute("SELECT * FROM invoices WHERE content_hash = ?", (h,)).fetchone()

    def live_with_number(self, ico: str, number: str) -> sqlite3.Row | None:
        return self.db.execute(
            "SELECT * FROM invoices WHERE supplier_ico = ? AND number_key = ? AND decision != 'STOP'",
            (ico, number_key(number))).fetchone()

    def same_amount_and_date(self, inv: Invoice) -> sqlite3.Row | None:
        return self.db.execute(
            "SELECT * FROM invoices WHERE supplier_ico = ? AND total = ? AND issue_date = ? "
            "AND number_key != ? AND decision != 'STOP'",
            (inv.supplier_ico, str(inv.total), inv.issue_date, number_key(inv.invoice_number))).fetchone()

    def invoiced_qty(self, po_number: str | None) -> dict[str, Decimal]:
        """Quantities already billed for one purchase order by invoices that were not stopped."""
        if not po_number:
            return {}
        rows = self.db.execute(
            "SELECT l.sku, l.qty FROM invoice_lines l JOIN invoices i ON i.id = l.invoice_id "
            "WHERE l.po_number = ? AND i.decision != 'STOP'", (po_number,)).fetchall()
        out: dict[str, Decimal] = {}
        for r in rows:
            out[r["sku"]] = out.get(r["sku"], Decimal(0)) + Decimal(r["qty"])
        return out

    def insert(self, h: str, inv: Invoice, decision: str, findings_json: str,
               source: str | None = None, rules_version: str | None = None) -> int:
        """Invoice and its lines in one transaction: either both are saved or neither."""
        with self.db:
            cur = self.db.execute(
                "INSERT INTO invoices (content_hash, supplier_ico, invoice_number, number_key, issue_date, total,"
                " po_number, decision, findings_json, source, rules_version)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (h, inv.supplier_ico, inv.invoice_number, number_key(inv.invoice_number), inv.issue_date,
                 str(inv.total), inv.po_number, decision, findings_json, source, rules_version))
            self.db.executemany(
                "INSERT INTO invoice_lines (invoice_id, po_number, sku, qty) VALUES (?, ?, ?, ?)",
                [(cur.lastrowid, inv.po_number, l.sku, str(l.qty)) for l in inv.lines])
            return cur.lastrowid

    def history(self, limit: int = 50) -> list[dict]:
        rows = self.db.execute(
            "SELECT id, supplier_ico, invoice_number, issue_date, total, po_number, decision, findings_json,"
            " source, rules_version, created_at FROM invoices ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [{**{k: r[k] for k in r.keys() if k != "findings_json"},
                 "reasons": [f"{f['code']}: {f['message']}" for f in json.loads(r["findings_json"])]}
                for r in rows]
