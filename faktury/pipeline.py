"""One entry point: process(invoice) -> Result. Deterministic, same input = same output."""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

from . import checks
from .guard import scan_document
from .models import (Decision, Finding, Invoice, Line, PurchaseOrder, Receipt, Result, Severity,
                     Supplier)
from .store import Store, content_hash


@dataclass
class Books:
    """Company master data: who we buy from, what we ordered, what arrived."""
    suppliers: dict[str, Supplier] = field(default_factory=dict)
    orders: dict[str, PurchaseOrder] = field(default_factory=dict)
    receipts: dict[str, Receipt] = field(default_factory=dict)

    @classmethod
    def load(cls, folder: str | Path) -> "Books":
        folder = Path(folder)
        read = lambda name: json.loads((folder / name).read_text(encoding="utf-8"))
        b = cls()
        for s in read("suppliers.json"):
            b.suppliers[s["ico"]] = Supplier(s["ico"], s["name"], s["bank_account"].replace(" ", ""))
        for o in read("orders.json"):
            b.orders[o["po_number"]] = PurchaseOrder(
                o["po_number"], o["supplier_ico"], o["currency"],
                tuple(Line.from_dict(x) for x in o["lines"]))
        for r in read("receipts.json"):
            b.receipts[r["po_number"]] = Receipt(
                r["po_number"], {k: Decimal(str(v)) for k, v in r["received"].items()})
        return b


def decide(findings: list[Finding]) -> Decision:
    severities = {f.severity for f in findings}
    if Severity.STOP in severities:
        return Decision.STOP
    if Severity.REVIEW in severities:
        return Decision.REVIEW
    return Decision.APPROVE


def _to_json(findings: list[Finding]) -> str:
    return json.dumps([{"code": f.code, "severity": f.severity.value, "message": f.message,
                        "evidence": f.evidence} for f in findings], ensure_ascii=False)


def _from_json(s: str) -> list[Finding]:
    return [Finding(d["code"], Severity(d["severity"]), d["message"], d["evidence"]) for d in json.loads(s)]


RULES_VERSION = "2026-10-20"

READ_FROM_PHOTO = ("READ_FROM_PHOTO", Severity.REVIEW,
                   "Data přečetla AI z fotky. Z obrázku se pletou podobné číslice (8 a 6, 0 a 8), "
                   "proto fotka nikdy nejde k úhradě bez kontroly člověkem. Z PDF tohle neplatí.")


def process(inv: Invoice, books: Books, store: Store, raw_text: str | None = None,
            source: str | None = None) -> Result:
    """raw_text = the original document. Only the guard scans it; the rules never read text.
    source = where the data came from: "pdf", "photo", "manual" or None (for the audit trail)."""
    h = content_hash(inv)

    # 1) Idempotence: the exact same document again changes nothing.
    old = store.by_hash(h)
    if old is not None:
        findings = _from_json(old["findings_json"]) + [Finding(
            "REPLAY", Severity.INFO,
            f"Tuhle fakturu už jsme zpracovali (záznam #{old['id']}). Nic nového nevzniklo, "
            "platba se nevytvoří podruhé.", {"invoice_id": old["id"]})]
        return Result(Decision(old["decision"]), findings, replay=True, invoice_id=old["id"])

    # 2) Rules.
    findings: list[Finding] = []
    findings += checks.check_ico(inv)
    findings += checks.check_arithmetic(inv)
    findings += checks.check_amounts(inv)
    findings += checks.check_supplier(inv, books.suppliers.get(inv.supplier_ico))
    po = books.orders.get(inv.po_number) if inv.po_number else None
    findings += checks.check_three_way(inv, po, books.receipts.get(inv.po_number or ""),
                                       store.invoiced_qty(inv.po_number))
    if raw_text is not None:
        findings += scan_document(raw_text)
    if source == "photo":
        findings.append(Finding(*READ_FROM_PHOTO, {"source": source}))

    # 3) Duplicates against what we already have.
    dup = store.live_with_number(inv.supplier_ico, inv.invoice_number)
    if dup is not None:
        findings.append(Finding(
            "DUPLICATE_NUMBER", Severity.STOP,
            f"Faktura {inv.invoice_number} od tohoto dodavatele už existuje (záznam #{dup['id']}), "
            "ale s jiným obsahem. Buď duplicita, nebo někdo fakturu upravil.", {"invoice_id": dup["id"]}))
    near = store.same_amount_and_date(inv)
    if near is not None:
        findings.append(Finding(
            "POSSIBLE_DUPLICATE", Severity.REVIEW,
            f"Stejný dodavatel, stejná částka a den jako faktura {near['invoice_number']} "
            f"(záznam #{near['id']}). Možná dvojí fakturace.", {"invoice_id": near["id"]}))

    decision = decide(findings)

    # 4) Save. The UNIQUE index is the last line of defence (two requests at once).
    try:
        invoice_id = store.insert(h, inv, decision.value, _to_json(findings), source, RULES_VERSION)
    except sqlite3.IntegrityError:
        if store.by_hash(h) is not None:  # the same document won the race -> replay
            return process(inv, books, store, raw_text, source)
        findings.append(Finding("DUPLICATE_NUMBER", Severity.STOP,
                                "Stejná faktura se zpracovává podruhé současně.", {}))
        decision = Decision.STOP
        invoice_id = store.insert(h, inv, decision.value, _to_json(findings), source, RULES_VERSION)
    return Result(decision, findings, invoice_id=invoice_id)
