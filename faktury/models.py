"""Data types. Money is always Decimal, never float."""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from enum import Enum


class Severity(str, Enum):
    INFO = "INFO"
    REVIEW = "REVIEW"
    STOP = "STOP"


class Decision(str, Enum):
    APPROVE = "APPROVE"
    REVIEW = "REVIEW"
    STOP = "STOP"


MAX_ABS = Decimal("1e15")  # far above any real invoice, far below Decimal's overflow


def to_decimal(value) -> Decimal:
    """Parse a number from outside input. NaN, Infinity, junk and absurdly large values -> ValueError,
    never an arithmetic crash later in the pipeline (RT-KF-04, RT-KF-05)."""
    try:
        d = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError(f"Neplatné číslo: {str(value)[:40]!r}") from None
    if not d.is_finite() or abs(d) > MAX_ABS:
        raise ValueError(f"Číslo mimo povolený rozsah: {str(value)[:40]!r}")
    return d


def money(value) -> Decimal:
    # Czech invoices round half up (0.125 -> 0.13); Python's default would give 0.12
    return to_decimal(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


# Cyrillic / Greek capitals that look identical to a Latin capital. We fold them to Latin so a
# look-alike character cannot smuggle a second invoice past the duplicate check (RT-KF homoglyph).
_CONFUSABLES = str.maketrans({
    "А": "A", "В": "B", "Е": "E", "К": "K", "М": "M", "Н": "H", "О": "O", "Р": "P", "С": "C",
    "Т": "T", "У": "Y", "Х": "X", "Ѕ": "S", "І": "I", "Ј": "J",
    "Α": "A", "Β": "B", "Ε": "E", "Ζ": "Z", "Η": "H", "Ι": "I", "Κ": "K", "Μ": "M", "Ν": "N",
    "Ο": "O", "Ρ": "P", "Τ": "T", "Υ": "Y", "Χ": "X",
})
# Keep digits, Latin A-Z, and Czech accented capitals. Everything else (spaces, punctuation,
# unmapped exotic letters) is dropped. Accents are kept, so 'Č' stays distinct from 'C' and two
# genuinely different numbers are not merged into one (the old NFKD form collapsed them).
_NUMBER_DROP = re.compile(r"[^0-9A-ZÁČĎÉĚÍŇÓŘŠŤÚŮÝŽ]")


def number_key(invoice_number: str) -> str:
    """'FV-2026/0458', 'fv 2026 0458' and 'FV20260458' are the same invoice for duplicate checks.
    Look-alike Cyrillic/Greek letters are folded to Latin so they cannot hide a duplicate; accents
    are preserved so distinct numbers are not wrongly merged."""
    s = unicodedata.normalize("NFC", invoice_number).upper().translate(_CONFUSABLES)
    return _NUMBER_DROP.sub("", s)


def account_key(account: str) -> str:
    """'000000-2400123456/2010' and '2400123456/2010' are the same Czech account."""
    a = account.replace(" ", "")
    if "/" not in a:
        return a
    acc, bank = a.rsplit("/", 1)
    prefix, number = acc.split("-", 1) if "-" in acc else ("", acc)
    prefix, number = prefix.lstrip("0"), number.lstrip("0")
    return f"{prefix}-{number}/{bank}" if prefix else f"{number}/{bank}"


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
        return cls(sku=d["sku"], qty=to_decimal(d["qty"]), unit_price=money(d["unit_price"]))


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
            vat_rate=to_decimal(d["vat_rate"]),
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
