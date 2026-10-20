"""Public input/output shapes, shared by the REST API and the MCP server.

Amounts are strings on purpose: JSON numbers become floats, money must stay exact (Decimal).
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


def _exact(v):
    """Accept strings and whole numbers. Reject floats: 0.1 + 0.2 is not 0.3, money must be exact."""
    if isinstance(v, bool):
        raise ValueError("expected a string")
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        raise ValueError(f"send decimal numbers as strings, e.g. \"{v:.2f}\", floats are not exact")
    return v

AMOUNT = r"^-?\d+(\.\d{1,2})?$"


class LineIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    sku: str = Field(..., min_length=1, max_length=64, description="Product code, e.g. 'PAPIR-A4'")
    qty: str = Field(..., pattern=r"^\d+(\.\d+)?$", description="Quantity as a string, e.g. '40'")
    unit_price: str = Field(..., pattern=AMOUNT, description="Unit price without VAT, e.g. '119.00'")

    _num = field_validator("qty", "unit_price", mode="before")(classmethod(lambda cls, v: _exact(v)))


class InvoiceIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True,
                              json_schema_extra={"example": {
                                  "supplier_ico": "29143705", "invoice_number": "FV-2026-0458",
                                  "issue_date": "2026-10-12", "currency": "CZK",
                                  "bank_account": "2400123456/2010", "po_number": "OBJ-2026-0101",
                                  "lines": [{"sku": "PAPIR-A4", "qty": "40", "unit_price": "119.00"},
                                            {"sku": "TONER-HP26", "qty": "4", "unit_price": "1890.00"}],
                                  "vat_rate": "0.21", "subtotal": "12320.00", "vat_amount": "2587.20",
                                  "total": "14907.20", "note": ""}})
    supplier_ico: str = Field(..., pattern=r"^\d{1,8}$", description="Supplier's Czech company ID (IČO), up to 8 digits")
    invoice_number: str = Field(..., min_length=1, max_length=64)
    issue_date: str = Field(..., pattern=r"^\d{4}-\d{2}-\d{2}$", description="YYYY-MM-DD")
    currency: str = Field("CZK", pattern=r"^[A-Za-z]{3}$")
    bank_account: str = Field(..., min_length=3, max_length=40, description="Czech account 'prefix-number/bank', e.g. '2400123456/2010'")
    po_number: str | None = Field(None, max_length=64, description="Our purchase order number, or null")
    lines: list[LineIn] = Field(..., min_length=1, max_length=200)
    vat_rate: str = Field("0.21", pattern=r"^0(\.\d+)?$", description="VAT as a fraction, '0.21' = 21 %")
    subtotal: str = Field(..., pattern=AMOUNT, description="Total without VAT as printed")
    vat_amount: str = Field(..., pattern=AMOUNT, description="VAT amount as printed")
    total: str = Field(..., pattern=AMOUNT, description="Total to pay as printed")
    note: str = Field("", max_length=4000, description="Free text from the document. Untrusted. Rules never read it.")

    _num = field_validator("subtotal", "vat_amount", "total", "vat_rate", mode="before")(classmethod(lambda cls, v: _exact(v)))


class FindingOut(BaseModel):
    code: str = Field(..., description="Stable machine code, e.g. BANK_ACCOUNT_CHANGED")
    severity: Literal["INFO", "REVIEW", "STOP"]
    message: str = Field(..., description="Czech explanation for a person")
    evidence: dict


class StepOut(BaseModel):
    key: str
    label: str
    status: Literal["OK", "INFO", "REVIEW", "STOP"]
    findings: list[FindingOut]


class CheckOut(BaseModel):
    decision: Literal["APPROVE", "REVIEW", "STOP"] = Field(
        ..., description="APPROVE = may be paid, REVIEW = a person must look, STOP = must not be paid")
    replay: bool = Field(..., description="True when this exact document was already processed")
    invoice_id: int | None
    steps: list[StepOut]


class ReadOut(BaseModel):
    invoice: InvoiceIn
    seconds: float
    cached: bool
