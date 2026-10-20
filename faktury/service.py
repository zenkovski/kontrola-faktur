"""One service used by the web page, the REST API and the MCP server. No logic lives in the adapters."""
from __future__ import annotations

import json
import os
import threading
from dataclasses import asdict
from pathlib import Path

from .extract import ExtractionError
from .extract_cli import read_file
from .models import Invoice
from .pipeline import RULES_VERSION, Books, process
from .store import Store

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
DOC_TYPES = {".pdf", ".png", ".jpg", ".jpeg", ".webp"}

STEPS = [
    ("ico", "IČO dodavatele", {"ICO_INVALID"}),
    ("math", "Součty a DPH", {"SUBTOTAL_MISMATCH", "VAT_MISMATCH", "TOTAL_MISMATCH"}),
    ("supplier", "Dodavatel a bankovní účet", {"SUPPLIER_UNKNOWN", "BANK_ACCOUNT_CHANGED"}),
    ("match", "Objednávka a převzaté zboží", {"PO_MISSING", "PO_SUPPLIER_MISMATCH", "CURRENCY_MISMATCH",
                                               "RECEIPT_MISSING", "LINE_NOT_ORDERED", "QTY_OVER_ORDERED",
                                               "QTY_OVER_RECEIVED", "PRICE_MISMATCH"}),
    ("dup", "Duplicity", {"DUPLICATE_NUMBER", "POSSIBLE_DUPLICATE", "REPLAY"}),
    ("amounts", "Kladné částky", {"TOTAL_NOT_POSITIVE", "LINE_NOT_POSITIVE"}),
    ("guard", "Text pro AI v dokumentu", {"TEXT_FOR_AI"}),
    ("source", "Spolehlivost čtení", {"READ_FROM_PHOTO", "EXTRACTION_FAILED"}),
]

RULES = """Decision: STOP if any check says STOP, else REVIEW if any says REVIEW, else APPROVE.
The decision is made by deterministic code. Free text on the invoice is never read by the rules.

Checks and their codes:
- ico: ICO_INVALID (STOP) - Czech company ID fails the modulo 11 check digit.
- math: SUBTOTAL_MISMATCH, VAT_MISMATCH, TOTAL_MISMATCH (STOP) - printed numbers do not add up.
- supplier: SUPPLIER_UNKNOWN (REVIEW); BANK_ACCOUNT_CHANGED (STOP) - account differs from the one on file.
- match (invoice vs purchase order vs goods receipt): PO_MISSING, RECEIPT_MISSING, LINE_NOT_ORDERED,
  QTY_OVER_ORDERED, QTY_OVER_RECEIVED, PRICE_MISMATCH (>1 %) (REVIEW); PO_SUPPLIER_MISMATCH, CURRENCY_MISMATCH (STOP).
- dup: REPLAY (INFO, the exact same document again, nothing new is created); DUPLICATE_NUMBER (STOP);
  POSSIBLE_DUPLICATE (REVIEW, same supplier, amount and day).
- amounts: TOTAL_NOT_POSITIVE, LINE_NOT_POSITIVE (REVIEW) - zero or negative invoice or line.
- guard: TEXT_FOR_AI (REVIEW) - the document contains instructions aimed at an AI.
- source: READ_FROM_PHOTO (REVIEW) - data read from a photo is never paid without a person;
  EXTRACTION_FAILED (REVIEW) - the AI could not read the document, nothing is decided automatically.
Quantities are summed per product across lines and across earlier invoices for the same order.
Invoice numbers are compared without spaces, dashes and case ("FV-2026/0458" = "fv 2026 0458").
"""


class Service:
    def __init__(self, db_path: str = ":memory:"):
        self.books = Books.load(DATA)
        self.db_path = db_path
        self.store = Store(db_path)
        self.lock = threading.Lock()

    def reset(self) -> None:
        with self.lock:
            self.store = Store(self.db_path) if self.db_path == ":memory:" else self._wipe()

    def _wipe(self) -> Store:
        self.store.db.execute("DELETE FROM invoices")
        self.store.db.commit()
        return self.store

    def check(self, data: dict, document_text: str | None = None, source: str | None = None) -> dict:
        inv = Invoice.from_dict(data)
        # the guard scans the real document text when we have it, plus whatever the reader copied out
        text = "\n".join(x for x in (document_text, inv.note) if x) or None
        with self.lock:
            r = process(inv, self.books, self.store, raw_text=text, source=source)
        findings = [{"code": f.code, "severity": f.severity.value, "message": f.message, "evidence": f.evidence}
                    for f in r.findings]
        return self._result(r.decision.value, r.replay, r.invoice_id, findings)

    @staticmethod
    def _result(decision: str, replay: bool, invoice_id, findings: list[dict]) -> dict:
        steps = []
        for key, label, codes in STEPS:
            mine = [f for f in findings if f["code"] in codes]
            status = ("STOP" if any(f["severity"] == "STOP" for f in mine) else
                      "REVIEW" if any(f["severity"] == "REVIEW" for f in mine) else
                      "INFO" if mine else "OK")
            steps.append({"key": key, "label": label, "status": status, "findings": mine})
        return {"decision": decision, "replay": replay, "invoice_id": invoice_id, "steps": steps,
                "rules_version": RULES_VERSION}

    def read(self, path: str | Path) -> dict:
        """AI reads one document. Only files inside the allowed folders can be opened."""
        path = Path(path).resolve()
        if path.suffix.lower() not in DOC_TYPES:
            raise ValueError(f"Unsupported file type {path.suffix}. Use PDF, PNG, JPG or WEBP.")
        roots = allowed_roots()
        if not any(path.is_relative_to(r) for r in roots):
            raise PermissionError(f"Files can be read only from: {', '.join(map(str, roots))}. "
                                  "Copy the invoice there or set KONTROLA_FAKTUR_FILES.")
        if not path.is_file():
            raise FileNotFoundError(f"File not found: {path}")
        inv, raw, seconds, cached = read_file(path, DATA / "extracted")
        is_pdf = path.suffix.lower() == ".pdf"
        return {"invoice": raw, "seconds": round(seconds, 1), "cached": cached,
                "source": "pdf" if is_pdf else "photo",
                "document_text": pdf_text(path) if is_pdf else None}

    def read_and_check(self, path: str | Path) -> dict:
        """Read with AI, then check. If the AI fails, the answer is REVIEW, never an error or APPROVE."""
        try:
            read = self.read(path)
        except ExtractionError as e:
            finding = {"code": "EXTRACTION_FAILED", "severity": "REVIEW", "evidence": {},
                       "message": f"AI fakturu nepřečetla ({e}). Musí ji zpracovat člověk."}
            return {"read": None, "check": self._result("REVIEW", False, None, [finding])}
        check = self.check(read["invoice"], read["document_text"], read["source"])
        return {"read": {k: v for k, v in read.items() if k != "document_text"}, "check": check}

    def history(self, limit: int = 50) -> list[dict]:
        with self.lock:
            return self.store.history(limit)

    def demo_list(self) -> list[dict]:
        expected = json.loads((DATA / "expected.json").read_text(encoding="utf-8"))
        return [{"id": k, "title": v["title"], "story": v["story"], "expected": v["decision"],
                 "pdf": str(DATA / "pdf" / f"{k}.pdf")} for k, v in expected.items()]

    def demo_invoice(self, demo_id: str) -> dict:
        p = DATA / "invoices" / f"{Path(demo_id).name}.json"
        if not p.exists():
            ids = ", ".join(d["id"] for d in self.demo_list())
            raise KeyError(f"Unknown demo id '{demo_id}'. Use one of: {ids}")
        return json.loads(p.read_text(encoding="utf-8"))

    def reference(self) -> dict:
        b = self.books
        return {
            "suppliers": [asdict(s) for s in b.suppliers.values()],
            "orders": [{"po_number": o.po_number, "supplier_ico": o.supplier_ico, "currency": o.currency,
                        "lines": [{"sku": l.sku, "qty": str(l.qty), "unit_price": str(l.unit_price)} for l in o.lines]}
                       for o in b.orders.values()],
            "receipts": [{"po_number": r.po_number, "received": {k: str(v) for k, v in r.received.items()}}
                         for r in b.receipts.values()],
        }


def pdf_text(path: Path) -> str:
    """Text layer of a PDF, read by code (no AI). The guard scans it for instructions aimed at an AI."""
    import fitz

    with fitz.open(path) as doc:
        return "\n".join(page.get_text() for page in doc)[:20000]


def allowed_roots() -> list[Path]:
    env = os.environ.get("KONTROLA_FAKTUR_FILES")
    if env:
        return [Path(p).resolve() for p in env.split(os.pathsep) if p]
    return [DATA.resolve(), (ROOT / ".tmp" / "uploads").resolve(), (ROOT / "inbox").resolve()]


def default_db() -> str:
    """MCP and API keep history in a file so duplicates are caught across restarts."""
    path = Path(os.environ.get("KONTROLA_FAKTUR_DB", ROOT / ".tmp" / "history.db"))
    path.parent.mkdir(parents=True, exist_ok=True)
    return str(path)
