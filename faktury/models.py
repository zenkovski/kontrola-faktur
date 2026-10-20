"""Data types. Money is always Decimal, never float."""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum


class Severity(str, Enum):
    INFO = "INFO"
    REVIEW = "REVIEW"
    STOP = "STOP"


class Decision(str, Enum):
    APPROVE = "APPROVE"
    REVIEW = "REVIEW"
    STOP = "STOP"


def money(value) -> Decimal:
    return Decimal(str(value)).quantize(Decimal("0.01"))


@dataclass(frozen=True)
class Line:
    sku: str
    qty: Decimal
    unit_price: Decimal  # without VAT

    @property
    def amount(self) -> Decimal:
        return money(self.qty * self.unit_price)

    @classmethod
    def from_dict(cls, d: dict) -> "Line":
        return cls(sku=d["sku"], qty=Decimal(str(d["qty"])), unit_price=money(d["unit_price"]))


@dataclass(frozen=True)
class Invoice:
    supplier_ico: str
    invoice_number: str
    issue_date: str
    currency: str
    bank_account: str
    po_number: str | None
    lines: tuple[Line, ...]
    vat_rate: Decimal  # e.g. 0.21
    subtotal: Decimal
    vat_amount: Decimal
    total: Decimal
    note: str = ""  # free text from the document; untrusted, never interpreted by the core

    @classmethod
    def from_dict(cls, d: dict) -> "Invoice":
        return cls(
            supplier_ico=str(d["supplier_ico"]).strip(),
            invoice_number=str(d["invoice_number"]).strip(),
            issue_date=d["issue_date"],
            currency=d["currency"].upper(),
            bank_account=d["bank_account"].replace(" ", ""),
            po_number=d.get("po_number"),
            lines=tuple(Line.from_dict(x) for x in d["lines"]),
            vat_rate=Decimal(str(d["vat_rate"])),
            subtotal=money(d["subtotal"]),
            vat_amount=money(d["vat_amount"]),
            total=money(d["total"]),
            note=d.get("note", ""),
        )


@dataclass(frozen=True)
class Supplier:
    ico: str
    name: str
    bank_account: str


@dataclass(frozen=True)
class PurchaseOrder:
    po_number: str
    supplier_ico: str
    currency: str
    lines: tuple[Line, ...]


@dataclass(frozen=True)
class Receipt:
    po_number: str
    received: dict[str, Decimal]  # sku -> qty received


@dataclass(frozen=True)
class Finding:
    code: str
    severity: Severity
    message: str  # Czech, shown to the visitor
    evidence: dict = field(default_factory=dict)


@dataclass
class Result:
    decision: Decision
    findings: list[Finding]
    replay: bool = False  # True = same document seen before, nothing new created
    invoice_id: int | None = None
