"""SQLite store. The database, not the code, guarantees one row per document.

Two keys:
- content_hash: the exact same document again -> replay (return old result, create nothing).
- (supplier_ico, invoice_number): same number, different content -> duplicate / tampering.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import asdict

from .models import Invoice

SCHEMA = """
CREATE TABLE IF NOT EXISTS invoices (
    id            INTEGER PRIMARY KEY,
    content_hash  TEXT NOT NULL UNIQUE,
    supplier_ico  TEXT NOT NULL,
    invoice_number TEXT NOT NULL,
    issue_date    TEXT NOT NULL,
    total         TEXT NOT NULL,
    decision      TEXT NOT NULL,
    findings_json TEXT NOT NULL,
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);
-- only one accepted (non-STOP) invoice per supplier + number
CREATE UNIQUE INDEX IF NOT EXISTS one_live_number
    ON invoices (supplier_ico, invoice_number) WHERE decision != 'STOP';
"""


def content_hash(inv: Invoice) -> str:
    data = json.dumps(asdict(inv), sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha256(data.encode()).hexdigest()


class Store:
    def __init__(self, path: str = ":memory:"):
        # web server threads share one connection; the caller serialises access with a lock
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    def by_hash(self, h: str) -> sqlite3.Row | None:
        return self.db.execute("SELECT * FROM invoices WHERE content_hash = ?", (h,)).fetchone()

    def live_with_number(self, ico: str, number: str) -> sqlite3.Row | None:
        return self.db.execute(
            "SELECT * FROM invoices WHERE supplier_ico = ? AND invoice_number = ? AND decision != 'STOP'",
            (ico, number)).fetchone()

    def same_amount_and_date(self, inv: Invoice) -> sqlite3.Row | None:
        return self.db.execute(
            "SELECT * FROM invoices WHERE supplier_ico = ? AND total = ? AND issue_date = ? "
            "AND invoice_number != ? AND decision != 'STOP'",
            (inv.supplier_ico, str(inv.total), inv.issue_date, inv.invoice_number)).fetchone()

    def insert(self, h: str, inv: Invoice, decision: str, findings_json: str) -> int:
        with self.db:
            cur = self.db.execute(
                "INSERT INTO invoices (content_hash, supplier_ico, invoice_number, issue_date, total,"
                " decision, findings_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (h, inv.supplier_ico, inv.invoice_number, inv.issue_date, str(inv.total),
                 decision, findings_json))
            return cur.lastrowid
